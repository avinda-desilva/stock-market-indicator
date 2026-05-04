"""
Ranking Engine — runs every minute via APScheduler.

Scoring formula:
    score = (mentions_1h * 3) + (mentions_24h * 1.5) + (sentiment * 2) + (price_change_pct * 2)

Spike boost: if mentions_1h > (mentions_24h_avg_per_hour * 2), multiply score by 1.5.

Results are written to Redis:
    trending:global          → top-10 across all sectors
    trending:<Sector>        → top-10 per sector  (e.g. trending:Technology)
Each key is a JSON array of RankedTicker objects, TTL = 90 s.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention

logger = logging.getLogger(__name__)

CACHE_TTL = 90  # seconds
TOP_N = 10


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
# DB helpers
# ---------------------------------------------------------------------------

async def _fetch_mention_counts(db: AsyncSession, since: datetime) -> dict[str, int]:
    """Return {symbol: count} for mentions whose article was published after *since*."""
    rows = await db.execute(
        select(TickerMention.ticker, func.count(TickerMention.id))
        .join(Article, Article.id == TickerMention.article_id)
        .where(Article.timestamp >= since)
        .group_by(TickerMention.ticker)
    )
    return {r[0]: r[1] for r in rows}


async def _fetch_avg_sentiment(db: AsyncSession, since: datetime) -> dict[str, float]:
    """Return {symbol: avg_sentiment} for mentions whose article was published after *since*."""
    rows = await db.execute(
        select(TickerMention.ticker, func.avg(TickerMention.sentiment))
        .join(Article, Article.id == TickerMention.article_id)
        .where(Article.timestamp >= since)
        .where(TickerMention.sentiment.is_not(None))
        .group_by(TickerMention.ticker)
    )
    return {r[0]: float(r[1]) for r in rows}


async def _fetch_all_tickers(db: AsyncSession) -> list[Ticker]:
    result = await db.execute(select(Ticker))
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_score(
    mentions_1h: int,
    mentions_24h: int,
    sentiment: float,
    price_change_pct: float,
) -> float:
    return (
        mentions_1h * 3
        + mentions_24h * 1.5
        + sentiment * 2
        + price_change_pct * 2
    )


def _apply_spike_boost(score: float, mentions_1h: int, mentions_24h: int) -> float:
    """Boost score 1.5× when the last hour's mentions exceed twice the hourly average."""
    hourly_avg = mentions_24h / 24 if mentions_24h else 0
    if hourly_avg > 0 and mentions_1h > (hourly_avg * 2):
        return score * 1.5
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
    """Compute scores for all tickers and push results to Redis."""
    now = datetime.now(tz=timezone.utc)
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)

    async with AsyncSessionLocal() as db:
        tickers = await _fetch_all_tickers(db)
        if not tickers:
            logger.info("ranking_engine: no tickers in DB, skipping")
            return

        mentions_1h = await _fetch_mention_counts(db, cutoff_1h)
        mentions_24h = await _fetch_mention_counts(db, cutoff_24h)
        sentiment_24h = await _fetch_avg_sentiment(db, cutoff_24h)

    ranked: list[dict] = []
    for ticker in tickers:
        sym = ticker.symbol
        m1h = mentions_1h.get(sym, 0)
        m24h = mentions_24h.get(sym, 0)
        sent = sentiment_24h.get(sym, 0.0)
        # price_change_pct is not yet stored per ticker; default to 0.0 until
        # market_ingestor persists it (placeholder for Stage 5 integration).
        price_chg = 0.0

        score = _compute_score(m1h, m24h, sent, price_chg)
        score = _apply_spike_boost(score, m1h, m24h)

        ranked.append(
            {
                "symbol": sym,
                "sector": ticker.sector or "Unknown",
                "company_name": ticker.company_name or "",
                "score": round(score, 4),
                "mentions_1h": m1h,
                "mentions_24h": m24h,
                "sentiment": round(sent, 4),
                "price_change_pct": price_chg,
                "spike": (
                    m24h > 0 and m1h > ((m24h / 24) * 2)
                ),
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
        "ranking_engine: scored %d tickers, %d sectors",
        len(ranked),
        len(sectors),
    )
