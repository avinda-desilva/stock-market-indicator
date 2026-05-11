from datetime import datetime

from pydantic import BaseModel, Field


class NormalizedItem(BaseModel):
    """Unified schema returned by every ingestion worker."""

    id: str = Field(description="Source-scoped unique ID (e.g. newsapi-<uuid>)")
    source: str = Field(description="Origin: newsapi | reddit | twitter | polygon")
    title: str | None = None
    content: str
    timestamp: datetime
    url: str | None = None
    tickers: list[str] = Field(default_factory=list, description="Extracted ticker symbols")
    sentiment: float | None = Field(
        default=None, description="LLM-scored polarity in [-1.0, 1.0]"
    )
