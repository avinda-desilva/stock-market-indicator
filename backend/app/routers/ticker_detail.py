"""
Ticker detail endpoints:
  GET /ticker/{symbol}               — profile + trend metrics
  GET /ticker/{symbol}?window=6h|24h|3d|7d — scoped to time window (default 24h)
  GET /ticker/{symbol}/news          — recent articles mentioning the ticker
  GET /ticker/{symbol}/news?window=  — articles within time window
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention
from app.schemas.search import NewsItem, TickerDetailResponse, TickerNewsResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ticker", tags=["ticker"])

WINDOW_HOURS: dict[str, int] = {"6h": 6, "24h": 24, "3d": 72, "7d": 168}


@router.get("/{symbol}/news", response_model=TickerNewsResponse, summary="Recent news for a ticker")
async def get_ticker_news(
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200, description="Max articles to return"),
    window: str = Query(default="24h", description="Time window: 6h | 24h | 3d | 7d"),
    db: AsyncSession = Depends(get_db),
):
    """Return articles that mention the given ticker within the requested time window."""
    if window not in WINDOW_HOURS:
        raise HTTPException(status_code=422, detail=f"Invalid window '{window}'. Use one of: {list(WINDOW_HOURS)}")

    sym = symbol.upper()
    ticker = await db.get(Ticker, sym)
    if ticker is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{sym}' not found")

    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS[window])

    from sqlalchemy import func as sqlfunc
    canonical = (
        select(sqlfunc.min(Article.id).label("canonical_id"))
        .join(TickerMention, TickerMention.article_id == Article.id)
        .where(TickerMention.ticker == sym)
        .where(Article.timestamp >= cutoff)
        .group_by(sqlfunc.lower(Article.title))
        .order_by(sqlfunc.min(Article.id).desc())
        .limit(limit)
        .subquery()
    )

    rows = await db.execute(
        select(Article.id, Article.source, Article.title, Article.url, Article.timestamp, TickerMention.sentiment)
        .join(TickerMention, TickerMention.article_id == Article.id)
        .where(Article.id.in_(select(canonical.c.canonical_id)))
        .where(TickerMention.ticker == sym)
        .distinct(Article.id)
        .order_by(Article.id.desc())
    )

    seen_titles: set[str] = set()
    news: list[NewsItem] = []
    for row in rows:
        key = row.title.lower().strip() if row.title else str(row.id)
        if key in seen_titles:
            continue
        seen_titles.add(key)
        news.append(NewsItem(
            id=row.id,
            source=row.source,
            title=row.title,
            url=row.url,
            timestamp=row.timestamp,
            sentiment=round(row.sentiment, 4) if row.sentiment is not None else None,
        ))

    return TickerNewsResponse(symbol=sym, news=news)


@router.get("/{symbol}", response_model=TickerDetailResponse, summary="Ticker profile + trend metrics")
async def get_ticker_detail(
    symbol: str,
    window: str = Query(default="24h", description="Time window: 6h | 24h | 3d | 7d"),
    db: AsyncSession = Depends(get_db),
):
    """Return profile and trend metrics for a single ticker symbol scoped to the time window."""
    if window not in WINDOW_HOURS:
        raise HTTPException(status_code=422, detail=f"Invalid window '{window}'. Use one of: {list(WINDOW_HOURS)}")

    sym = symbol.upper()
    ticker = await db.get(Ticker, sym)
    if ticker is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{sym}' not found")

    now = datetime.now(tz=timezone.utc)
    hours = WINDOW_HOURS[window]
    cutoff = now - timedelta(hours=hours)
    cutoff_1h = now - timedelta(hours=1)

    agg_1h = await db.execute(
        select(func.count(TickerMention.id))
        .join(Article, Article.id == TickerMention.article_id)
        .where(TickerMention.ticker == sym)
        .where(Article.timestamp >= cutoff_1h)
    )
    mentions_1h: int = agg_1h.scalar_one() or 0

    agg_window = await db.execute(
        select(
            func.count(TickerMention.id).label("cnt"),
            func.avg(TickerMention.sentiment).label("sent"),
        )
        .join(Article, Article.id == TickerMention.article_id)
        .where(TickerMention.ticker == sym)
        .where(Article.timestamp >= cutoff)
    )
    row = agg_window.one()
    mentions_window: int = row.cnt or 0
    avg_sentiment: float | None = float(row.sent) if row.sent is not None else None

    sent_val = avg_sentiment or 0.0
    score = round(mentions_1h * 3 + mentions_window * 1.5 + sent_val * 2, 4)
    hourly_avg = mentions_window / hours if mentions_window else 0
    spike = mentions_1h > (hourly_avg * 2) if hourly_avg > 0 else False

    return TickerDetailResponse(
        symbol=sym,
        company_name=ticker.company_name,
        sector=ticker.sector,
        mentions_1h=mentions_1h,
        mentions_24h=mentions_window,
        sentiment=round(avg_sentiment, 4) if avg_sentiment is not None else None,
        score=score,
        spike=spike,
    )
