from datetime import datetime

from pydantic import BaseModel


class TickerScore(BaseModel):
    symbol: str
    company_name: str | None
    sector: str | None
    score: float
    mentions_24h: int
    sentiment: float | None


class NewsItem(BaseModel):
    id: int
    source: str
    title: str | None
    url: str | None
    timestamp: datetime
    sentiment: float | None


class TrendData(BaseModel):
    symbol: str
    mentions_1h: int
    mentions_24h: int
    sentiment: float | None
    score: float
    spike: bool


class SearchResponse(BaseModel):
    query: str
    intent: str
    tickers: list[TickerScore]
    news: list[NewsItem]
    trend_data: list[TrendData]


class TickerDetailResponse(BaseModel):
    symbol: str
    company_name: str | None
    sector: str | None
    mentions_1h: int
    mentions_24h: int
    sentiment: float | None
    score: float
    spike: bool
    gauge_sentiment_15m: float | None = None


class TickerNewsResponse(BaseModel):
    symbol: str
    news: list[NewsItem]
