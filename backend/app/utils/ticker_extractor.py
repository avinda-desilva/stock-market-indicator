"""Ticker extraction via FinBERT-NER with DB cross-reference.

The NER pipeline is a module-level singleton loaded once at startup via
`init_ner_pipeline()`. All inference runs in a ThreadPoolExecutor so async
callers are never blocked.
"""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticker import Ticker

logger = logging.getLogger(__name__)

# Loaded once at startup; None means model unavailable → fall back to regex.
_ner_pipeline: Any = None

# One shared executor for all blocking NER calls.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ner")

# FinBERT-NER's maximum input tokens; we truncate text before passing it.
_MAX_CHARS = 1500  # ~512 tokens at ~3 chars/token — safe conservative limit

# Fallback regex + stopwords used when the model is unavailable.
_TICKER_RE = re.compile(r"\$([A-Z]{2,5})\b|\b([A-Z]{2,5})\b")
_STOPWORDS: frozenset[str] = frozenset(
    {
        "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN",
        "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US",
        "WE", "AND", "ARE", "FOR", "GET", "HAS", "HAD", "HIM", "HIS", "HOW",
        "ITS", "LET", "MAY", "NEW", "NOT", "NOW", "OFF", "OUR", "OUT", "OWN",
        "SAY", "SEE", "SET", "SHE", "THE", "TOO", "TWO", "WAS", "WAY", "WHO",
        "WHY", "YET", "YOU", "ALL", "BIG", "CAN", "DID", "HER", "ILL", "OLD",
        "ONE", "OWE", "PUT", "RAN", "RUN", "SIT", "SIX", "TEN", "YES", "YOUR",
        "BEEN", "BOTH", "CALL", "CAME", "COME", "DOES", "EACH", "EVEN", "FROM",
        "GAVE", "GIVE", "GOES", "GONE", "GOOD", "HAVE", "HELP", "HERE", "HIGH",
        "INTO", "JUST", "KEEP", "KIND", "KNOW", "LAST", "LESS", "LIKE", "LONG",
        "LOOK", "MADE", "MAKE", "MANY", "MORE", "MOST", "MOVE", "MUCH", "MUST",
        "NEED", "NEXT", "ONLY", "OPEN", "OVER", "PART", "PAST", "PLAN", "PLAY",
        "READ", "REAL", "SAID", "SAME", "SEEM", "SHOW", "SIDE", "SOME", "SOON",
        "STAY", "SUCH", "TAKE", "TELL", "THAN", "THAT", "THEM", "THEN", "THEY",
        "THIS", "THUS", "TIME", "TOLD", "TOOK", "TURN", "USED", "VERY", "WANT",
        "WELL", "WENT", "WERE", "WHAT", "WHEN", "WITH", "WORD", "WORK", "YEAR",
        "IPO", "ETF", "CEO", "CFO", "COO", "CTO", "SEC", "NYSE", "NASDAQ",
        "GDP", "FED", "IMF", "EUR", "USD", "GBP", "YEN", "OTC", "ALT",
    }
)


def init_ner_pipeline() -> None:
    """Load the FinBERT-NER pipeline into the module singleton.

    Called once from the FastAPI lifespan. Safe to call multiple times
    (no-op if already loaded). Logs a warning and leaves the singleton as
    None on failure so the regex fallback takes over.
    """
    global _ner_pipeline
    if _ner_pipeline is not None:
        return
    try:
        from transformers import pipeline  # type: ignore[import]

        _ner_pipeline = pipeline(
            "ner",
            model="dslim/bert-base-NER",
            aggregation_strategy="simple",
            device=-1,  # CPU
        )
        logger.info("dslim/bert-base-NER pipeline loaded.")
    except Exception:
        logger.warning("FinBERT-NER unavailable — falling back to regex extraction.", exc_info=True)


_LEGAL_SUFFIXES = re.compile(
    r"\b(Inc\.?|Corp\.?|Co\.?|Ltd\.?|LLC|plc|Group|Company|Corporation|"
    r"Incorporated|Limited|Holdings?|Laboratories?|Systems?|Technologies?|"
    r"Platforms?|Partners?|Bancorp|Bancshares?|Financial|Services?|"
    r"Pharmaceuticals?|Energy|Motors?|Airlines?|Networks?|Solutions?|"
    r"Enterprises?|Industries?|International|Global|Worldwide|Capital|"
    r"Ventures?|Associates?|The)\b\.?",
    re.IGNORECASE,
)
_NAME_KEYWORD_BLOCKLIST: frozenset[str] = frozenset(
    {"linde", "deere", "ford", "general", "advanced", "american", "next",
     "united", "home", "procter", "gamble", "walt", "morgan", "bank", "visa"}
)


def _company_keywords(name: str) -> list[str]:
    cleaned = _LEGAL_SUFFIXES.sub(" ", name)
    cleaned = re.sub(r"[^A-Za-z0-9 ]", " ", cleaned)
    tokens = [t.lower() for t in cleaned.split() if len(t) >= 4]
    tokens = [t for t in tokens if t not in _NAME_KEYWORD_BLOCKLIST]
    return sorted(set(tokens), key=len, reverse=True)


def _build_name_patterns(tickers: list[tuple[str, str]]) -> list[tuple[re.Pattern[str], str]]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for symbol, company_name in tickers:
        for kw in _company_keywords(company_name):
            try:
                pat = re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
                patterns.append((pat, symbol))
            except re.error:
                pass
    return patterns


def _run_ner(text: str) -> list[str]:
    """Run NER inference (blocking) and return candidate strings."""
    truncated = text[:_MAX_CHARS]
    if not truncated.strip():
        return []
    results = _ner_pipeline(truncated)
    candidates: list[str] = []
    for ent in results:
        label = (ent.get("entity_group") or ent.get("entity") or "").upper()
        if label in ("ORG", "TICKER", "B-ORG", "I-ORG"):
            word = ent.get("word", "").strip()
            if word:
                candidates.append(word)
    return candidates


def _run_regex(text: str) -> list[str]:
    """Fallback: extract uppercase tokens not in the stopword list."""
    found: list[str] = []
    seen: set[str] = set()
    for m in _TICKER_RE.finditer(text):
        symbol = (m.group(1) or m.group(2)).upper()
        if symbol not in _STOPWORDS and symbol not in seen:
            seen.add(symbol)
            found.append(symbol)
    return found


async def extract_tickers_db(text: str, db: AsyncSession) -> list[str]:
    """Return ticker symbols present in `text` that also exist in the DB.

    Uses dslim/bert-base-NER when available, regex otherwise.

    Two resolution passes are applied:
      1. Symbol pass — NER/regex tokens that are already valid ticker-length
         uppercase strings are matched directly against `tickers.symbol`.
      2. Name pass — multi-word ORG entities (e.g. "Apple Inc") are matched
         against `tickers.company_name` using keyword search, the same way
         the original regex path did for truncated article previews.

    Only symbols that exist in the DB are returned, so no bare English words
    slip through.
    """
    if not text or not text.strip():
        return []

    loop = asyncio.get_event_loop()

    # --- Candidate extraction ---
    if _ner_pipeline is not None:
        try:
            raw_candidates: list[str] = await loop.run_in_executor(
                _executor, _run_ner, text
            )
        except Exception:
            logger.warning("NER inference failed; falling back to regex.", exc_info=True)
            raw_candidates = _run_regex(text)
    else:
        raw_candidates = _run_regex(text)

    if not raw_candidates:
        return []

    # Split candidates into symbol-like tokens vs multi-word ORG strings.
    symbol_candidates: set[str] = set()
    org_phrases: list[str] = []
    for c in raw_candidates:
        token = c.lstrip("$").upper()
        if re.fullmatch(r"[A-Z]{1,5}", token):
            symbol_candidates.add(token)
        else:
            # Keep original casing for company-name keyword matching
            org_phrases.append(c.strip())

    matched: set[str] = set()

    # --- Pass 1: direct symbol lookup ---
    if symbol_candidates:
        result = await db.execute(
            select(Ticker.symbol).where(Ticker.symbol.in_(symbol_candidates))
        )
        matched.update(row[0] for row in result.all())

    # --- Pass 2: company-name keyword lookup for multi-word ORG entities ---
    if org_phrases:
        all_tickers = await db.execute(select(Ticker.symbol, Ticker.company_name))
        ticker_rows: list[tuple[str, str]] = list(all_tickers.all())
        name_patterns = _build_name_patterns(ticker_rows)
        # Test each ORG phrase against every company-name pattern
        combined = " ".join(org_phrases)
        for pat, symbol in name_patterns:
            if symbol not in matched and pat.search(combined):
                matched.add(symbol)

    return list(matched)
