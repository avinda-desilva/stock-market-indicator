import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete

from app.database import AsyncSessionLocal
from app.models.article import Article
from app.workers import news_ingestor, social_ingestor, market_ingestor
from app.workers import alphavantage_ingestor, yahoo_rss_ingestor, gdelt_ingestor
from app.workers import stocktwits_ingestor, googlenews_ingestor, finnhub_ingestor
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


async def _run_gdelt():
    async with AsyncSessionLocal() as db:
        items = await gdelt_ingestor.run(db)
        logger.info("gdelt_ingestor ingested %d items", len(items))


async def _run_stocktwits():
    async with AsyncSessionLocal() as db:
        items = await stocktwits_ingestor.run(db)
        logger.info("stocktwits_ingestor ingested %d items", len(items))


async def _run_googlenews():
    async with AsyncSessionLocal() as db:
        items = await googlenews_ingestor.run(db)
        logger.info("googlenews_ingestor ingested %d items", len(items))


async def _run_finnhub_general():
    async with AsyncSessionLocal() as db:
        items = await finnhub_ingestor.run_general(db)
        logger.info("finnhub_ingestor/general-news ingested %d items", len(items))


async def _run_finnhub_company():
    async with AsyncSessionLocal() as db:
        items = await finnhub_ingestor.run_company(db)
        logger.info("finnhub_ingestor/company-news ingested %d items", len(items))


async def _run_finnhub_insider():
    async with AsyncSessionLocal() as db:
        items = await finnhub_ingestor.run_insider(db)
        logger.info("finnhub_ingestor/insider-sentiment ingested %d items", len(items))


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
    # PAUSED: LLM ingestion suspended while tuning Docker/Ollama resource limits.
    # _scheduler.add_job(_run_news, CronTrigger(minute="*/30"), id="news_ingestor")
    # Social: every 15 minutes
    _scheduler.add_job(_run_social, CronTrigger(minute="*/15"), id="social_ingestor")
    # Market news: every hour
    _scheduler.add_job(_run_market, CronTrigger(minute=0), id="market_ingestor")
    # Alpha Vantage: every 6 hours (25 req/day free cap; 6 topics × 4 runs = 24 req/day)
    _scheduler.add_job(_run_alphavantage, CronTrigger(hour="*/6"), id="alphavantage_ingestor")
    # Yahoo Finance RSS: PAUSED — calls analyze_article per article (~500/run, saturates Ollama)
    # _scheduler.add_job(_run_yahoo_rss, CronTrigger(minute=30), id="yahoo_rss_ingestor")
    # GDELT GKG: every 15 minutes (aligns with GDELT's own 15-min snapshot cadence)
    _scheduler.add_job(_run_gdelt, CronTrigger(minute="*/15"), id="gdelt_ingestor")
    # StockTwits retail sentiment: every 20 minutes (sequential per-ticker fetch with 2s sleep)
    _scheduler.add_job(_run_stocktwits, CronTrigger(minute="*/20"), id="stocktwits_ingestor")
    # Google News RSS: every hour at :45 (15 sector queries, no API key)
    _scheduler.add_job(_run_googlenews, CronTrigger(minute=45), id="googlenews_ingestor")
    # Finnhub general market news: every 30 min (1 call/run, far under 60 req/min free cap)
    _scheduler.add_job(_run_finnhub_general, CronTrigger(minute="*/30"), id="finnhub_general")
    # Finnhub company news: PAUSED — calls analyze_article per (ticker, article) pair
    # _scheduler.add_job(_run_finnhub_company, CronTrigger(hour="*/2", minute=10), id="finnhub_company")
    # Finnhub insider sentiment: every 6 hours (≤50 ticker calls/run, token-bucket throttled)
    _scheduler.add_job(_run_finnhub_insider, CronTrigger(hour="*/6", minute=20), id="finnhub_insider")
    # Ranking engine: every minute
    _scheduler.add_job(_run_ranking, CronTrigger(minute="*"), id="ranking_engine")
    # Nightly cleanup: 11:59 PM UTC — purge articles older than 7 days
    _scheduler.add_job(_run_cleanup, CronTrigger(hour=23, minute=59, timezone="UTC"), id="cleanup")
    _scheduler.start()
    logger.info("Scheduler started")


async def shutdown_scheduler():
    _scheduler.shutdown(wait=False)
