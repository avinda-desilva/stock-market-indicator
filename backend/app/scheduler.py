import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete

from app.database import AsyncSessionLocal
from app.models.article import Article
from app.workers import news_ingestor, social_ingestor, market_ingestor
from app.workers import alphavantage_ingestor, yahoo_rss_ingestor
from app.workers.ranking_engine import run_ranking

logger = logging.getLogger(__name__)
_scheduler = AsyncIOScheduler()


async def _run_news():
    async with AsyncSessionLocal() as db:
        items = await news_ingestor.run(db)
        logger.info("news_ingestor ingested %d items", len(items))


async def _run_social():
    async with AsyncSessionLocal() as db:
        items = await social_ingestor.run(db)
        logger.info("social_ingestor ingested %d items", len(items))


async def _run_market():
    async with AsyncSessionLocal() as db:
        items = await market_ingestor.run(db)
        logger.info("market_ingestor ingested %d items", len(items))


async def _run_alphavantage():
    async with AsyncSessionLocal() as db:
        items = await alphavantage_ingestor.run(db)
        logger.info("alphavantage_ingestor ingested %d items", len(items))


async def _run_yahoo_rss():
    async with AsyncSessionLocal() as db:
        items = await yahoo_rss_ingestor.run(db)
        logger.info("yahoo_rss_ingestor ingested %d items", len(items))


async def _run_ranking():
    try:
        await run_ranking()
    except Exception:
        logger.exception("ranking_engine failed")


async def _run_cleanup():
    """Delete articles (and cascaded ticker_mentions) older than 7 days."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(Article).where(Article.timestamp < cutoff)
            )
            await db.commit()
            logger.info("cleanup: deleted %d articles older than 7 days", result.rowcount)
    except Exception:
        logger.exception("cleanup job failed")


def start_scheduler():
    # News: every 30 minutes
    _scheduler.add_job(_run_news, CronTrigger(minute="*/30"), id="news_ingestor")
    # Social: every 15 minutes
    _scheduler.add_job(_run_social, CronTrigger(minute="*/15"), id="social_ingestor")
    # Market news: every hour
    _scheduler.add_job(_run_market, CronTrigger(minute=0), id="market_ingestor")
    # Alpha Vantage: every 6 hours (25 req/day free cap; 6 topics × 4 runs = 24 req/day)
    _scheduler.add_job(_run_alphavantage, CronTrigger(hour="*/6"), id="alphavantage_ingestor")
    # Yahoo Finance RSS: every hour (no key, no rate limit concern)
    _scheduler.add_job(_run_yahoo_rss, CronTrigger(minute=30), id="yahoo_rss_ingestor")
    # Ranking engine: every minute
    _scheduler.add_job(_run_ranking, CronTrigger(minute="*"), id="ranking_engine")
    # Nightly cleanup: 11:59 PM UTC — purge articles older than 7 days
    _scheduler.add_job(_run_cleanup, CronTrigger(hour=23, minute=59, timezone="UTC"), id="cleanup")
    _scheduler.start()
    logger.info("Scheduler started")


async def shutdown_scheduler():
    _scheduler.shutdown(wait=False)
