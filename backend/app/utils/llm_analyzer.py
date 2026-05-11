"""LLM-based article analysis via local Ollama.

Replaces ProsusAI/finbert (sentiment) and dslim/bert-base-NER (ticker extraction)
with a single Llama 3.1 8B call per (article, ticker) pair.

The semaphore in `_get_semaphore()` is the primary OOM guard: with 6.3 GB RAM and
an 8B 4-bit model (~4.7 GB), only one inference can run at a time. Callers that
exceed the semaphore cap queue and wait rather than spawning parallel requests.
"""

import asyncio
import json
import logging
import re
from typing import Optional

import httpx
from pydantic import BaseModel, field_validator

from app.config import settings

logger = logging.getLogger(__name__)

# ── Semaphore (module-level singleton) ────────────────────────────────────────
# Initialised lazily so the event loop exists when it is first accessed.
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.ollama_max_concurrency)
    return _semaphore


# ── Prompt template ───────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are a strict financial data extraction API. "
    "Your ONLY output must be a valid, parsable JSON object. "
    "Do not include markdown formatting, conversational text, or explanations."
)

_USER_PROMPT_TEMPLATE = """\
Analyze the following financial article to determine its relevance and sentiment toward the specific ticker symbol: {ticker}.

Article Text:
---
{article_text}
---

Task:
1. Determine if the company represented by {ticker} is a PRIMARY focus of the article. If it is a passing mention, an ad, or unrelated, set "is_relevant" to false.
2. Calculate the financial sentiment toward {ticker} strictly as a float between -1.0 (highly bearish) and 1.0 (highly bullish). Neutral is 0.0.
3. Write a concise 2-sentence summary of the article's core impact on {ticker}.

Output exactly this JSON structure and nothing else:
{{
  "ticker": "{ticker}",
  "is_relevant": true | false,
  "sentiment": float,
  "summary": "string"
}}"""

# Ollama truncation guard: Llama 3.1 has a 128k context window but on CPU
# we cap at ~3000 chars (~750 tokens) to keep latency under the timeout.
_MAX_ARTICLE_CHARS = 3000

# Maximum attempts before giving up on a single (article, ticker) pair.
_MAX_RETRIES = 2


# ── Response schema ───────────────────────────────────────────────────────────
class LLMAnalysis(BaseModel):
    ticker: str
    is_relevant: bool
    sentiment: float
    summary: str

    @field_validator("sentiment")
    @classmethod
    def clamp_sentiment(cls, v: float) -> float:
        return max(-1.0, min(1.0, round(v, 4)))


# ── JSON extraction helpers ───────────────────────────────────────────────────
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict:
    """Pull the first JSON object out of a potentially noisy LLM response."""
    # Try fenced code block first, then bare object, then the whole string.
    for candidate in [
        *_JSON_BLOCK_RE.findall(raw),
        *_JSON_OBJECT_RE.findall(raw),
        raw.strip(),
    ]:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError(f"No valid JSON found in LLM response: {raw[:200]!r}")


# ── Core async call ───────────────────────────────────────────────────────────
async def _call_ollama(prompt_user: str) -> str:
    """POST to /api/chat with system + user messages; return the assistant text."""
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt_user},
        ],
        "stream": False,
        "options": {
            # Deterministic output — we need strict JSON, not creative prose.
            "temperature": 0.0,
            "top_p": 1.0,
        },
    }
    async with httpx.AsyncClient(timeout=settings.ollama_timeout) as client:
        response = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        # /api/chat wraps the reply in message.content
        return data["message"]["content"]


# ── Public API ─────────────────────────────────────────────────────────────────
async def analyze_article(
    ticker: str,
    article_text: str,
) -> Optional[LLMAnalysis]:
    """Analyze one (ticker, article) pair via Ollama.

    Returns None if the article is irrelevant to the ticker or if all retries
    are exhausted. Callers should treat None as "skip this mention".

    The semaphore ensures at most `OLLAMA_MAX_CONCURRENCY` requests are
    in-flight simultaneously, preventing OOM on the Ollama container.
    """
    if not ticker or not article_text or not article_text.strip():
        return None

    truncated = article_text[:_MAX_ARTICLE_CHARS]
    prompt = _USER_PROMPT_TEMPLATE.format(ticker=ticker, article_text=truncated)

    sem = _get_semaphore()

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with sem:
                raw = await _call_ollama(prompt)

            parsed = _extract_json(raw)
            analysis = LLMAnalysis.model_validate(parsed)

            if not analysis.is_relevant:
                logger.debug("LLM: %s not primary focus — skipping.", ticker)
                return None

            return analysis

        except httpx.TimeoutException:
            logger.warning(
                "Ollama timeout on %s (attempt %d/%d).", ticker, attempt, _MAX_RETRIES
            )
        except httpx.HTTPStatusError as exc:
            logger.error("Ollama HTTP error %s for %s.", exc.response.status_code, ticker)
            break  # Non-transient; don't retry.
        except (ValueError, KeyError) as exc:
            logger.warning(
                "LLM JSON parse failed for %s (attempt %d/%d): %s",
                ticker, attempt, _MAX_RETRIES, exc,
            )

    logger.error("All %d attempts failed for ticker %s — skipping mention.", _MAX_RETRIES, ticker)
    return None


async def analyze_batch(
    ticker: str,
    articles: list[str],
) -> list[Optional[LLMAnalysis]]:
    """Analyze multiple articles for a single ticker concurrently.

    Concurrency is still bounded by the semaphore inside `analyze_article`,
    so this is safe to call even with large batches (e.g. 750 Yahoo RSS items).
    Tasks queue up and drain one-at-a-time when OLLAMA_MAX_CONCURRENCY=1.
    """
    tasks = [analyze_article(ticker, text) for text in articles]
    return list(await asyncio.gather(*tasks))
