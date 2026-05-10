import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.schemas.normalized import NormalizedItem
from app.workers import market_ingestor, news_ingestor, social_ingestor
from app.workers import alphavantage_ingestor, yahoo_rss_ingestor, gdelt_ingestor
from app.workers import stocktwits_ingestor, googlenews_ingestor, finnhub_ingestor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingestors"])


@router.post("/news-ingestor", response_model=list[NormalizedItem])
async def news_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a NewsAPI ingestion run."""
    return await news_ingestor.run(db)


@router.post("/social-ingestor", response_model=list[NormalizedItem])
async def social_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a Reddit social ingestion run."""
    return await social_ingestor.run(db)


@router.post("/market-ingestor", response_model=list[NormalizedItem])
async def market_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a Polygon.io market news ingestion run."""
    return await market_ingestor.run(db)


@router.post("/alphavantage-ingestor", response_model=list[NormalizedItem])
async def alphavantage_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger an Alpha Vantage news & sentiment ingestion run."""
    return await alphavantage_ingestor.run(db)


@router.post("/yahoo-rss-ingestor", response_model=list[NormalizedItem])
async def yahoo_rss_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a Yahoo Finance RSS ingestion run across all tickers."""
    return await yahoo_rss_ingestor.run(db)


@router.post("/gdelt-ingestor", response_model=list[NormalizedItem])
async def gdelt_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a GDELT 2.0 GKG ingestion run (current 15-min snapshot)."""
    return await gdelt_ingestor.run(db)


@router.post("/stocktwits-ingestor", response_model=list[NormalizedItem])
async def stocktwits_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a StockTwits retail-sentiment ingestion run (top-20 trending + all DB tickers)."""
    return await stocktwits_ingestor.run(db)


@router.post("/googlenews-ingestor", response_model=list[NormalizedItem])
async def googlenews_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a Google News RSS ingestion run across 15 sector search queries."""
    return await googlenews_ingestor.run(db)


@router.post("/finnhub-ingestor", response_model=list[NormalizedItem])
async def finnhub_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger a Finnhub ingestion run (general news + company news + insider sentiment)."""
    return await finnhub_ingestor.run(db)


@router.post("/finnhub-general", response_model=list[NormalizedItem])
async def finnhub_general_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger Finnhub general market news only."""
    return await finnhub_ingestor.run_general(db)


@router.post("/finnhub-company", response_model=list[NormalizedItem])
async def finnhub_company_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger Finnhub per-ticker company news only."""
    return await finnhub_ingestor.run_company(db)


@router.post("/finnhub-insider", response_model=list[NormalizedItem])
async def finnhub_insider_ingest(db: AsyncSession = Depends(get_db)):
    """Trigger Finnhub insider sentiment only."""
    return await finnhub_ingestor.run_insider(db)


async def _run_all_ingestors():
    """Run all ingestors concurrently, each with its own DB session. Errors are logged, not raised."""
    async def _run(worker):
        try:
            async with AsyncSessionLocal() as db:
                await worker.run(db)
        except Exception as exc:
            logger.warning("Ingestor %s failed: %s", worker.__name__, exc)

    await asyncio.gather(
        _run(news_ingestor),
        _run(social_ingestor),
        _run(market_ingestor),
        _run(yahoo_rss_ingestor),
        _run(gdelt_ingestor),
        _run(stocktwits_ingestor),
        _run(googlenews_ingestor),
        _run(finnhub_ingestor),
        return_exceptions=True,
    )


@router.post("/all", summary="Trigger all ingestors concurrently (fire-and-forget)")
async def ingest_all(background_tasks: BackgroundTasks):
    """Kick off every ingestor in the background and return immediately."""
    background_tasks.add_task(_run_all_ingestors)
    return {"status": "started", "message": "All ingestors triggered in the background."}
