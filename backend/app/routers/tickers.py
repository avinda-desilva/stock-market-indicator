from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ticker import Ticker
from app.schemas.ticker import TickerCreate, TickerOut

router = APIRouter(prefix="/tickers", tags=["tickers"])


@router.get("", response_model=list[TickerOut])
async def list_tickers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Ticker).order_by(Ticker.symbol))
    return result.scalars().all()


@router.post("", response_model=TickerOut, status_code=201)
async def create_ticker(payload: TickerCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.get(Ticker, payload.symbol)
    if existing:
        raise HTTPException(status_code=409, detail="Ticker already exists")
    ticker = Ticker(**payload.model_dump())
    db.add(ticker)
    await db.commit()
    await db.refresh(ticker)
    return ticker
