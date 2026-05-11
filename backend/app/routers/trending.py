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
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention
from app.utils.query_pipeline import SECTOR_KEYWORDS
from app.workers.ranking_engine import _get_redis, SPIKE_Z_THRESHOLD, SPIKE_BOOST

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
    """Compute top-N trending tickers directly from Postgres using the same
    z-score formula as the ranking engine, scoped to an arbitrary time window."""

    sector_filter = "AND t.sector = :sector" if sector else ""

    sql = text(f"""
    WITH window_mentions AS (
        SELECT tm.ticker, COUNT(tm.id) AS mentions
        FROM ticker_mentions tm
        JOIN articles a ON a.id = tm.article_id
        JOIN tickers t ON t.symbol = tm.ticker
        WHERE a.timestamp >= NOW() AT TIME ZONE 'UTC' - INTERVAL '1 hour' * :hours
          AND a.source NOT IN ('stocktwits')
          {sector_filter}
        GROUP BY tm.ticker
    ),
    hourly_buckets AS (
        SELECT tm.ticker,
               date_trunc('hour', a.timestamp) AS hour_bucket,
               COUNT(tm.id) AS bucket_mentions
        FROM ticker_mentions tm
        JOIN articles a ON a.id = tm.article_id
        JOIN tickers t ON t.symbol = tm.ticker
        WHERE a.timestamp >= NOW() AT TIME ZONE 'UTC' - INTERVAL '7 days'
          AND a.source NOT IN ('stocktwits')
          {sector_filter}
        GROUP BY tm.ticker, hour_bucket
    ),
    ticker_stats AS (
        SELECT ticker, AVG(bucket_mentions) AS mu, STDDEV(bucket_mentions) AS sigma
        FROM hourly_buckets
        GROUP BY ticker
    ),
    recent_1h AS (
        SELECT tm.ticker, COUNT(tm.id) AS mentions_1h
        FROM ticker_mentions tm
        JOIN articles a ON a.id = tm.article_id
        WHERE a.timestamp >= NOW() AT TIME ZONE 'UTC' - INTERVAL '1 hour'
          AND a.source NOT IN ('stocktwits')
          AND tm.ticker IN (SELECT ticker FROM window_mentions)
        GROUP BY tm.ticker
    ),
    sentiment_window AS (
        SELECT tm.ticker,
               SUM(tm.sentiment * tm.source_weight) / NULLIF(SUM(tm.source_weight), 0) AS weighted_sentiment
        FROM ticker_mentions tm
        JOIN articles a ON a.id = tm.article_id
        WHERE a.timestamp >= NOW() AT TIME ZONE 'UTC' - INTERVAL '1 hour' * :hours
          AND tm.sentiment IS NOT NULL
          AND tm.ticker IN (SELECT ticker FROM window_mentions)
        GROUP BY tm.ticker
    )
    SELECT
        t.symbol,
        t.sector,
        t.company_name,
        wm.mentions                                       AS mentions_window,
        COALESCE(r1h.mentions_1h, 0)                     AS mentions_1h,
        COALESCE(sw.weighted_sentiment, 0.0)             AS sentiment,
        CASE
            WHEN ts.sigma IS NULL OR ts.sigma = 0 THEN 0.0
            ELSE (COALESCE(r1h.mentions_1h, 0) - ts.mu) / ts.sigma
        END AS z_score
    FROM window_mentions wm
    JOIN tickers t ON t.symbol = wm.ticker
    LEFT JOIN recent_1h r1h ON r1h.ticker = wm.ticker
    LEFT JOIN sentiment_window sw ON sw.ticker = wm.ticker
    LEFT JOIN ticker_stats ts ON ts.ticker = wm.ticker
    ORDER BY mentions_window DESC
    """)

    params: dict[str, Any] = {"hours": hours}
    if sector:
        params["sector"] = sector

    result = await db.execute(sql, params)
    rows = result.mappings().all()

    if not rows:
        return []

    ranked: list[dict[str, Any]] = []
    for row in rows:
        sym = row["symbol"]
        m_window = int(row["mentions_window"])
        m1h = int(row["mentions_1h"])
        sent = float(row["sentiment"])
        z = float(row["z_score"])

        score = (z * 2) + (m_window * 0.3) + (sent * 2)
        if z >= SPIKE_Z_THRESHOLD:
            score *= SPIKE_BOOST

        ranked.append({
            "symbol": sym,
            "sector": row["sector"] or "Unknown",
            "company_name": row["company_name"] or "",
            "score": round(score, 4),
            "mentions_1h": m1h,
            "mentions_24h": m_window,
            "sentiment": round(sent, 4),
            "price_change_pct": 0.0,
            "spike": z >= SPIKE_Z_THRESHOLD,
            "z_score": round(z, 4),
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
