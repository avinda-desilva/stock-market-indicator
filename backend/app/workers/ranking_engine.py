"""
Ranking Engine — runs every minute via APScheduler.

Scoring formula (blended Z-score + volume):
    z_score  = (mentions_1h - μ_hourly) / σ_hourly   over a 7-day rolling window
               Falls back to 0.0 when σ = 0 or no 7-day history exists.
    score    = (z_score * 2) + (mentions_24h * 0.15) + (sentiment * 2) + (price_change_pct * 2)

Spike boost: if z_score > 2.0 (2 σ above mean), multiply score by 1.3.

The Z-score detects relative spikes (micro-caps with sudden bursts rank high) while
the raw 24h volume term keeps established high-mention tickers visible even during
quiet hours when their 1h count is temporarily zero.

Stocktwits posts are excluded from all counts (no titles, inflate mention counts
without contributing to the news timeline).

Results are written to Redis:
    trending:global          → top-10 across all sectors
    trending:<Sector>        → top-10 per sector  (e.g. trending:Technology)
Each key is a JSON array of RankedTicker objects, TTL = 90 s.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.ticker import Ticker
from sqlalchemy import select

logger = logging.getLogger(__name__)

CACHE_TTL = 90  # seconds
TOP_N = 10
SPIKE_Z_THRESHOLD = 2.0   # σ above mean → spike boost
SPIKE_BOOST = 1.3         # conservative boost (was 1.5)
EXCLUDED_SOURCES = ("stocktwits",)


# ---------------------------------------------------------------------------
# Redis client (lazily created so import never blocks startup)
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            password=settings.redis_password or None,
            decode_responses=True,
        )
    return _redis


# ---------------------------------------------------------------------------
# Z-score query
# ---------------------------------------------------------------------------

# Language: PostgreSQL 14+
# Strategy:
#   1. Bucket every TickerMention into 1-hour slots over the last 7 days.
#   2. Compute per-ticker μ and σ across those hourly buckets using window functions.
#   3. Isolate the *current* hour bucket and derive its Z-score.
#   4. Also return weighted-average sentiment over the last 24 h for the score formula.
#
# The CTE is read-only and uses no locks.

_ZSCORE_SQL = text("""
WITH hourly_buckets AS (
    -- Count non-stocktwits mentions per ticker per hour over the last 7 days
    SELECT
        tm.ticker,
        date_trunc('hour', a.timestamp) AS hour_bucket,
        COUNT(tm.id)                    AS mentions
    FROM ticker_mentions tm
    JOIN articles a ON a.id = tm.article_id
    WHERE a.timestamp >= NOW() AT TIME ZONE 'UTC' - INTERVAL '7 days'
      AND a.source NOT IN ('stocktwits')
    GROUP BY tm.ticker, hour_bucket
),
ticker_stats AS (
    -- Rolling μ and σ per ticker across all 7-day hourly buckets
    SELECT
        ticker,
        AVG(mentions)    AS mu,
        STDDEV(mentions) AS sigma
    FROM hourly_buckets
    GROUP BY ticker
),
current_hour AS (
    -- Non-stocktwits mention count for the *current* hour only
    SELECT
        tm.ticker,
        COUNT(tm.id) AS mentions_1h
    FROM ticker_mentions tm
    JOIN articles a ON a.id = tm.article_id
    WHERE a.timestamp >= date_trunc('hour', NOW() AT TIME ZONE 'UTC')
      AND a.source NOT IN ('stocktwits')
    GROUP BY tm.ticker
),
mentions_24h AS (
    -- Raw 24-hour non-stocktwits mention count
    SELECT
        tm.ticker,
        COUNT(tm.id) AS mentions_24h
    FROM ticker_mentions tm
    JOIN articles a ON a.id = tm.article_id
    WHERE a.timestamp >= NOW() AT TIME ZONE 'UTC' - INTERVAL '24 hours'
      AND a.source NOT IN ('stocktwits')
    GROUP BY tm.ticker
),
sentiment_24h AS (
    -- Weighted-average sentiment over last 24 h (all sources — stocktwits has valid sentiment)
    SELECT
        tm.ticker,
        SUM(tm.sentiment * tm.source_weight)
            / NULLIF(SUM(tm.source_weight), 0) AS weighted_sentiment
    FROM ticker_mentions tm
    JOIN articles a ON a.id = tm.article_id
    WHERE a.timestamp >= NOW() AT TIME ZONE 'UTC' - INTERVAL '24 hours'
      AND tm.sentiment IS NOT NULL
    GROUP BY tm.ticker
)
SELECT
    t.symbol,
    t.sector,
    t.company_name,
    COALESCE(ch.mentions_1h,  0)        AS mentions_1h,
    COALESCE(m24.mentions_24h, 0)       AS mentions_24h,
    COALESCE(s.weighted_sentiment, 0.0) AS sentiment,
    -- Z-score: 0 when σ = 0 or no history exists
    CASE
        WHEN ts.sigma IS NULL OR ts.sigma = 0 THEN 0.0
        ELSE (COALESCE(ch.mentions_1h, 0) - ts.mu) / ts.sigma
    END AS z_score
FROM tickers t
LEFT JOIN current_hour  ch  ON ch.ticker  = t.symbol
LEFT JOIN mentions_24h  m24 ON m24.ticker = t.symbol
LEFT JOIN sentiment_24h s   ON s.ticker   = t.symbol
LEFT JOIN ticker_stats  ts  ON ts.ticker  = t.symbol
""")


async def _fetch_zscore_rows(db: AsyncSession) -> list[dict]:
    result = await db.execute(_ZSCORE_SQL)
    rows = result.mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_score(z_score: float, mentions_24h: int, sentiment: float, price_change_pct: float) -> float:
    # Z-score captures relative velocity; 24h volume keeps established tickers visible.
    return (z_score * 2) + (mentions_24h * 0.15) + (sentiment * 2) + (price_change_pct * 2)


def _apply_spike_boost(score: float, z_score: float) -> float:
    """Boost score 1.3× when the current hour is ≥ 2 σ above the rolling mean."""
    if z_score >= SPIKE_Z_THRESHOLD:
        return score * SPIKE_BOOST
    return score


# ---------------------------------------------------------------------------
# Cache writer
# ---------------------------------------------------------------------------

async def _write_cache(key: str, data: list[dict]) -> None:
    r = _get_redis()
    await r.set(key, json.dumps(data), ex=CACHE_TTL)
    logger.debug("Cached %s (%d tickers)", key, len(data))


# ---------------------------------------------------------------------------
# Main job
# ---------------------------------------------------------------------------

async def run_ranking() -> None:
    """Compute Z-score-based scores for all tickers and push results to Redis."""
    async with AsyncSessionLocal() as db:
        rows = await _fetch_zscore_rows(db)

    if not rows:
        logger.info("ranking_engine: no tickers in DB, skipping")
        return

    ranked: list[dict] = []
    for row in rows:
        sym      = row["symbol"]
        sector   = row["sector"] or "Unknown"
        name     = row["company_name"] or ""
        m1h      = int(row["mentions_1h"])
        m24h     = int(row["mentions_24h"])
        sent     = float(row["sentiment"])
        z        = float(row["z_score"])
        # price_change_pct not yet stored per ticker; placeholder until market_ingestor persists it.
        price_chg = 0.0

        score = _compute_score(z, m24h, sent, price_chg)
        score = _apply_spike_boost(score, z)

        ranked.append(
            {
                "symbol": sym,
                "sector": sector,
                "company_name": name,
                "score": round(score, 4),
                "mentions_1h": m1h,
                "mentions_24h": m24h,
                "sentiment": round(sent, 4),
                "price_change_pct": price_chg,
                "z_score": round(z, 4),
                "spike": z >= SPIKE_Z_THRESHOLD,
            }
        )

    ranked.sort(key=lambda x: x["score"], reverse=True)

    # --- global top-10 ---
    await _write_cache("trending:global", ranked[:TOP_N])

    # --- per-sector top-10 ---
    sectors: dict[str, list[dict]] = {}
    for item in ranked:
        sectors.setdefault(item["sector"], []).append(item)

    for sector, items in sectors.items():
        key = f"trending:{sector}"
        await _write_cache(key, items[:TOP_N])

    logger.info(
        "ranking_engine: scored %d tickers (z-score), %d sectors",
        len(ranked),
        len(sectors),
    )
