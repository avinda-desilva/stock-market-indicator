"""
Search router — GET /search?q=<query>

Pipeline:
1. parse_query()  →  QueryIntent (intent + entities)
2. Depending on intent, run targeted DB queries
3. Merge results into SearchResponse JSON
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention
from app.schemas.search import NewsItem, SearchResponse, TickerScore, TrendData
from app.utils.query_pipeline import parse_query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])

_RESULT_LIMIT = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _ticker_scores(
    db: AsyncSession,
    symbols: list[str] | None = None,
    sector: str | None = None,
    limit: int = _RESULT_LIMIT,
) -> list[TickerScore]:
    """Return scored tickers filtered by symbol list OR sector.

    Counts are based on article.timestamp (publication time) over the last 24 h
    so scores reflect actual news recency rather than ingestion time.
    """
    now = datetime.now(tz=timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    ticker_q = select(Ticker)
    if symbols:
        ticker_q = ticker_q.where(Ticker.symbol.in_(symbols))
    if sector:
        ticker_q = ticker_q.where(Ticker.sector == sector)

    ticker_rows = (await db.execute(ticker_q)).scalars().all()
    if not ticker_rows:
        return []

    syms = [t.symbol for t in ticker_rows]

    agg = await db.execute(
        select(
            TickerMention.ticker,
            func.count(TickerMention.id).label("mentions_24h"),
            func.avg(TickerMention.sentiment).label("avg_sent"),
        )
        .join(Article, Article.id == TickerMention.article_id)
        .where(TickerMention.ticker.in_(syms))
        .where(Article.timestamp >= cutoff_24h)
        .group_by(TickerMention.ticker)
    )
    agg_map = {r.ticker: r for r in agg}

    results: list[TickerScore] = []
    for t in ticker_rows:
        row = agg_map.get(t.symbol)
        m24 = row.mentions_24h if row else 0
        sent = float(row.avg_sent) if (row and row.avg_sent is not None) else 0.0
        score = round(m24 * 1.5 + sent * 2, 4)
        results.append(
            TickerScore(
                symbol=t.symbol,
                company_name=t.company_name,
                sector=t.sector,
                score=score,
                mentions_24h=m24,
                sentiment=round(sent, 4),
            )
        )

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:limit]


async def _news_for_tickers(
    db: AsyncSession,
    symbols: list[str],
    limit: int = _RESULT_LIMIT,
) -> list[NewsItem]:
    """Return recent unique articles (by URL) mentioning any of the given symbols."""
    if not symbols:
        return []

    # DISTINCT ON url deduplicates articles that were ingested multiple times.
    # We pick the row with the highest id (most recent ingestion) per url.
    rows = await db.execute(
        select(
            Article.id,
            Article.source,
            Article.title,
            Article.url,
            Article.timestamp,
            TickerMention.sentiment,
        )
        .join(TickerMention, TickerMention.article_id == Article.id)
        .where(TickerMention.ticker.in_(symbols))
        .distinct(Article.url)
        .order_by(Article.url, Article.id.desc(), Article.timestamp.desc())
        .limit(limit)
    )

    news: list[NewsItem] = [
        NewsItem(
            id=row.id,
            source=row.source,
            title=row.title,
            url=row.url,
            timestamp=row.timestamp,
            sentiment=round(row.sentiment, 4) if row.sentiment is not None else None,
        )
        for row in rows
    ]
    # Re-sort by timestamp desc after DISTINCT ON reordering
    news.sort(key=lambda n: n.timestamp, reverse=True)
    return news


async def _fts_articles(
    db: AsyncSession,
    keywords: list[str],
    limit: int = _RESULT_LIMIT,
) -> list[Article]:
    """Full-text search on article title + content via LIKE, deduped by URL."""
    if not keywords:
        return []

    conditions = [
        or_(
            Article.title.ilike(f"%{kw}%"),
            Article.content.ilike(f"%{kw}%"),
        )
        for kw in keywords
    ]
    # DISTINCT ON url so the same article ingested under multiple ids appears once.
    stmt = (
        select(Article)
        .where(or_(*conditions))
        .distinct(Article.url)
        .order_by(Article.url, Article.id.desc(), Article.timestamp.desc())
        .limit(limit)
    )
    articles = list((await db.execute(stmt)).scalars().all())
    # Re-sort by timestamp desc after DISTINCT ON reordering
    articles.sort(key=lambda a: a.timestamp, reverse=True)
    return articles


async def _trend_data_for_symbols(
    db: AsyncSession,
    symbols: list[str],
) -> list[TrendData]:
    """Return lightweight trend metrics using article.timestamp for time windows."""
    if not symbols:
        return []

    now = datetime.now(tz=timezone.utc)
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)

    agg_1h = await db.execute(
        select(TickerMention.ticker, func.count(TickerMention.id))
        .join(Article, Article.id == TickerMention.article_id)
        .where(TickerMention.ticker.in_(symbols))
        .where(Article.timestamp >= cutoff_1h)
        .group_by(TickerMention.ticker)
    )
    map_1h = {r[0]: r[1] for r in agg_1h}

    agg_24h = await db.execute(
        select(
            TickerMention.ticker,
            func.count(TickerMention.id).label("cnt"),
            func.avg(TickerMention.sentiment).label("sent"),
        )
        .join(Article, Article.id == TickerMention.article_id)
        .where(TickerMention.ticker.in_(symbols))
        .where(Article.timestamp >= cutoff_24h)
        .group_by(TickerMention.ticker)
    )
    map_24h = {r.ticker: r for r in agg_24h}

    trends: list[TrendData] = []
    for sym in symbols:
        m1h = map_1h.get(sym, 0)
        row24 = map_24h.get(sym)
        m24h = row24.cnt if row24 else 0
        sent = float(row24.sent) if (row24 and row24.sent is not None) else 0.0
        score = round(m1h * 3 + m24h * 1.5 + sent * 2, 4)
        hourly_avg = m24h / 24 if m24h else 0
        spike = m1h > (hourly_avg * 2) if hourly_avg > 0 else False
        trends.append(
            TrendData(
                symbol=sym,
                mentions_1h=m1h,
                mentions_24h=m24h,
                sentiment=round(sent, 4),
                score=score,
                spike=spike,
            )
        )

    trends.sort(key=lambda x: x.score, reverse=True)
    return trends


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("", response_model=SearchResponse, summary="Full-text financial search")
async def search(
    q: str = Query(..., min_length=1, description="Search query, e.g. 'AI stocks', 'AAPL', 'tech earnings'"),
    db: AsyncSession = Depends(get_db),
):
    """
    Query processing pipeline:
    - **sector_search** — tickers in matched sector + recent news + trend data
    - **ticker_lookup** — matched tickers + their news + trend data
    - **news_search / general_search** — FTS article search + related tickers + trend data
    """
    intent = parse_query(q)
    logger.info("search q=%r intent=%s sectors=%s tickers=%s", q, intent.intent, intent.sectors, intent.tickers)

    tickers: list[TickerScore] = []
    news: list[NewsItem] = []
    trend_data: list[TrendData] = []

    if intent.intent == "sector_search":
        for sector in intent.sectors:
            tickers += await _ticker_scores(db, sector=sector)
        symbols = [t.symbol for t in tickers]
        news = await _news_for_tickers(db, symbols)
        trend_data = await _trend_data_for_symbols(db, symbols)

    elif intent.intent == "ticker_lookup":
        tickers = await _ticker_scores(db, symbols=intent.tickers)
        if not tickers:
            # Partial company-name fallback
            for sym in intent.tickers:
                partial = await db.execute(
                    select(Ticker).where(
                        or_(
                            Ticker.symbol.ilike(f"%{sym}%"),
                            Ticker.company_name.ilike(f"%{sym}%"),
                        )
                    ).limit(5)
                )
                for t in partial.scalars():
                    if not any(ts.symbol == t.symbol for ts in tickers):
                        tickers.append(
                            TickerScore(
                                symbol=t.symbol,
                                company_name=t.company_name,
                                sector=t.sector,
                                score=0.0,
                                mentions_24h=0,
                                sentiment=None,
                            )
                        )
        symbols = [t.symbol for t in tickers]
        news = await _news_for_tickers(db, symbols)
        trend_data = await _trend_data_for_symbols(db, symbols)

    else:
        # news_search / general_search — FTS on articles, derive tickers from matches
        articles = await _fts_articles(db, intent.keywords)
        if articles:
            art_ids = [a.id for a in articles]
            mention_rows = await db.execute(
                select(TickerMention.ticker)
                .where(TickerMention.article_id.in_(art_ids))
                .distinct()
            )
            related_syms = [r[0] for r in mention_rows]
            tickers = await _ticker_scores(db, symbols=related_syms)
            trend_data = await _trend_data_for_symbols(db, related_syms)
            # Build news from the deduplicated FTS articles (sentiment comes from mentions)
            mention_sent_rows = await db.execute(
                select(TickerMention.article_id, func.avg(TickerMention.sentiment).label("sent"))
                .where(TickerMention.article_id.in_(art_ids))
                .group_by(TickerMention.article_id)
            )
            sent_map = {r.article_id: r.sent for r in mention_sent_rows}
            news = [
                NewsItem(
                    id=a.id,
                    source=a.source,
                    title=a.title,
                    url=a.url,
                    timestamp=a.timestamp,
                    sentiment=round(float(sent_map[a.id]), 4) if sent_map.get(a.id) is not None else None,
                )
                for a in articles
            ]
        else:
            # Last-resort: match company_name or symbol against keywords
            for kw in intent.keywords:
                partial = await db.execute(
                    select(Ticker).where(
                        or_(
                            Ticker.symbol.ilike(f"%{kw}%"),
                            Ticker.company_name.ilike(f"%{kw}%"),
                        )
                    ).limit(5)
                )
                for t in partial.scalars():
                    if not any(ts.symbol == t.symbol for ts in tickers):
                        tickers.append(
                            TickerScore(
                                symbol=t.symbol,
                                company_name=t.company_name,
                                sector=t.sector,
                                score=0.0,
                                mentions_24h=0,
                                sentiment=None,
                            )
                        )

    return SearchResponse(
        query=q,
        intent=intent.intent,
        tickers=tickers[:_RESULT_LIMIT],
        news=news[:_RESULT_LIMIT],
        trend_data=trend_data[:_RESULT_LIMIT],
    )
