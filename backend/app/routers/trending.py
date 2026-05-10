"""
Trending endpoints:
  GET /trending                     — top-10 globally (Redis for 24h; live DB for other windows)
  GET /trending?sector=AI           — top-10 for a sector
  GET /trending?window=6h|24h|3d|7d — filter by time window (default 24h)
  GET /trending/sector/{sector}     — top-10 for a sector (path style)
  GET /trending/sectors             — list all cached sector keys
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention
from app.utils.query_pipeline import SECTOR_KEYWORDS
from app.workers.ranking_engine import _get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/trending", tags=["trending"])

WINDOW_HOURS: dict[str, int] = {"6h": 6, "24h": 24, "3d": 72, "7d": 168}
TOP_N = 10


async def _read_cache(key: str) -> list[dict[str, Any]]:
    r = _get_redis()
    raw = await r.get(key)
    if raw is None:
        return []
    return json.loads(raw)


def _resolve_sector(sector_param: str) -> str:
    canonical = SECTOR_KEYWORDS.get(sector_param.lower())
    return canonical if canonical else sector_param


async def _live_trending(
    db: AsyncSession,
    hours: int,
    sector: str | None,
) -> list[dict[str, Any]]:
    """Compute top-N trending tickers directly from Postgres for arbitrary time windows."""
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(hours=hours)
    cutoff_1h = now - timedelta(hours=1)

    # Base filter: non-stocktwits mentions within the window
    base = (
        select(TickerMention.ticker, func.count(TickerMention.id).label("mentions"))
        .join(Article, Article.id == TickerMention.article_id)
        .where(Article.timestamp >= cutoff)
        .where(Article.source != "stocktwits")
    )
    if sector:
        base = base.join(Ticker, Ticker.symbol == TickerMention.ticker).where(
            Ticker.sector == sector
        )
    base = base.group_by(TickerMention.ticker)

    mention_rows = await db.execute(base)
    mention_map: dict[str, int] = {r[0]: r[1] for r in mention_rows}

    if not mention_map:
        return []

    # 1-hour mentions (for spike detection)
    m1h_rows = await db.execute(
        select(TickerMention.ticker, func.count(TickerMention.id).label("cnt"))
        .join(Article, Article.id == TickerMention.article_id)
        .where(Article.timestamp >= cutoff_1h)
        .where(Article.source != "stocktwits")
        .where(TickerMention.ticker.in_(list(mention_map.keys())))
        .group_by(TickerMention.ticker)
    )
    m1h_map: dict[str, int] = {r[0]: r[1] for r in m1h_rows}

    # Avg sentiment (all sources — stocktwits has valid sentiment scores)
    sent_rows = await db.execute(
        select(TickerMention.ticker, func.avg(TickerMention.sentiment).label("sent"))
        .join(Article, Article.id == TickerMention.article_id)
        .where(Article.timestamp >= cutoff)
        .where(TickerMention.sentiment.is_not(None))
        .where(TickerMention.ticker.in_(list(mention_map.keys())))
        .group_by(TickerMention.ticker)
    )
    sent_map: dict[str, float] = {r[0]: float(r[1]) for r in sent_rows}

    # Fetch ticker metadata
    ticker_rows = await db.execute(
        select(Ticker).where(Ticker.symbol.in_(list(mention_map.keys())))
    )
    ticker_meta: dict[str, Ticker] = {t.symbol: t for t in ticker_rows.scalars()}

    ranked: list[dict[str, Any]] = []
    for sym, mentions in mention_map.items():
        m1h = m1h_map.get(sym, 0)
        sent = sent_map.get(sym, 0.0)
        t = ticker_meta.get(sym)
        # Mirror ranking_engine formula: volume-anchored with mild spike detection
        score = float(m1h * 3 + mentions * 1.5 + sent * 2)
        hourly_avg = mentions / hours if mentions else 0
        spike = hourly_avg > 0 and m1h > (hourly_avg * 2)
        if spike:
            score *= 1.3
        ranked.append({
            "symbol": sym,
            "sector": (t.sector if t else None) or "Unknown",
            "company_name": (t.company_name if t else None) or "",
            "score": round(score, 4),
            "mentions_1h": m1h,
            "mentions_24h": mentions,
            "sentiment": round(sent, 4),
            "price_change_pct": 0.0,
            "spike": spike,
            "z_score": 0.0,
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:TOP_N]


@router.get("", summary="Top-10 trending tickers (global or by sector)")
async def get_trending(
    sector: str | None = Query(default=None),
    window: str = Query(default="24h", description="Time window: 6h | 24h | 3d | 7d"),
    db: AsyncSession = Depends(get_db),
):
    if window not in WINDOW_HOURS:
        raise HTTPException(status_code=422, detail=f"Invalid window '{window}'. Use one of: {list(WINDOW_HOURS)}")

    hours = WINDOW_HOURS[window]
    canonical_sector = _resolve_sector(sector) if sector else None

    # Use Redis cache only for the default 24h global view (ranking engine keeps it warm)
    if window == "24h" and sector is None:
        data = await _read_cache("trending:global")
        if data:
            return data

    data = await _live_trending(db, hours, canonical_sector)
    return data


@router.get("/sector/{sector}", summary="Top-10 tickers for a specific sector (path style)")
async def get_sector_trending(
    sector: str,
    window: str = Query(default="24h", description="Time window: 6h | 24h | 3d | 7d"),
    db: AsyncSession = Depends(get_db),
):
    if window not in WINDOW_HOURS:
        raise HTTPException(status_code=422, detail=f"Invalid window '{window}'. Use one of: {list(WINDOW_HOURS)}")

    canonical = _resolve_sector(sector)
    hours = WINDOW_HOURS[window]

    if window == "24h":
        data = await _read_cache(f"trending:{canonical}")
        if data:
            return data

    data = await _live_trending(db, hours, canonical)
    return data


@router.get("/sectors", summary="List all sectors currently in cache")
async def list_cached_sectors():
    r = _get_redis()
    keys = await r.keys("trending:*")
    sectors = [k.removeprefix("trending:") for k in keys]
    return {"sectors": sorted(sectors)}
