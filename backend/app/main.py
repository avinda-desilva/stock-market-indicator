import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine, Base
from app.utils.ticker_extractor import init_ner_pipeline
from app.routers.ingestors import router as ingest_router
from app.routers.search import router as search_router
from app.routers.ticker_detail import router as ticker_detail_router
from app.routers.tickers import router as tickers_router
from app.routers.trending import router as trending_router
from app.scheduler import start_scheduler, shutdown_scheduler
from app.workers.ranking_engine import run_ranking


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (Alembic handles production migrations)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, init_ner_pipeline)
    # Seed Redis immediately so the first frontend request doesn't see an empty cache
    try:
        await run_ranking()
    except Exception:
        pass
    start_scheduler()
    yield
    await shutdown_scheduler()
    await engine.dispose()


app = FastAPI(
    title="Stock Market Indicator API",
    version="0.2.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router)
app.include_router(tickers_router)
app.include_router(trending_router)
app.include_router(search_router)
app.include_router(ticker_detail_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
