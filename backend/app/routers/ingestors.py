from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.normalized import NormalizedItem
from app.workers import market_ingestor, news_ingestor, social_ingestor
from app.workers import alphavantage_ingestor, yahoo_rss_ingestor

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
