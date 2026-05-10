import asyncio
from typing import Optional

from transformers import pipeline, Pipeline

_finbert: Optional[Pipeline] = None


def _get_pipeline() -> Pipeline:
    global _finbert
    if _finbert is None:
        _finbert = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )
    return _finbert


def _score(text: str) -> float:
    result = _get_pipeline()(text[:2000])[0]  # pre-trim chars to bound tokenizer input
    label: str = result["label"].lower()
    confidence: float = result["score"]
    if label == "positive":
        return round(confidence, 4)
    if label == "negative":
        return round(-confidence, 4)
    return 0.0


def analyze_sentiment(text: str) -> float:
    """Return FinBERT polarity in [-1.0, 1.0]. 0.0 on empty input."""
    if not text or not text.strip():
        return 0.0
    return _score(text)


async def analyze_sentiment_async(text: str) -> float:
    """Non-blocking wrapper — runs FinBERT inference in a thread pool."""
    if not text or not text.strip():
        return 0.0
    return await asyncio.to_thread(_score, text)
