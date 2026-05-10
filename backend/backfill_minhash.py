"""
One-shot backfill: compute and store MinHash signatures for all articles
that currently have minhash_signature = NULL.

Run inside the backend container:
  python backfill_minhash.py
"""

import asyncio
import logging
import os

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 500


async def backfill() -> None:
    raw_url = os.environ["DATABASE_URL"]
    async_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Import after engine is ready so app bootstrap doesn't re-init anything
    from app.models.article import Article
    from app.utils.minhash_dedup import compute_minhash

    async with SessionLocal() as db:
        # Total count for progress reporting
        total_rows = (
            await db.execute(
                select(Article.id).where(Article.minhash_signature.is_(None))
            )
        ).all()
        total = len(total_rows)
        logger.info("Articles to backfill: %d", total)

        processed = 0

        while True:
            # Always fetch from offset 0 — updated rows drop out of the WHERE clause
            # so each fetch naturally advances the window without skipping rows.
            rows = (
                await db.execute(
                    select(Article.id, Article.content)
                    .where(Article.minhash_signature.is_(None))
                    .order_by(Article.id)
                    .limit(BATCH_SIZE)
                )
            ).all()

            if not rows:
                break

            for article_id, content in rows:
                sig = compute_minhash(content or "")
                await db.execute(
                    update(Article)
                    .where(Article.id == article_id)
                    .values(minhash_signature=sig)
                )

            await db.commit()
            processed += len(rows)
            logger.info("Backfilled %d / %d articles", processed, total)

    await engine.dispose()
    logger.info("Backfill complete — %d articles updated", processed)


if __name__ == "__main__":
    asyncio.run(backfill())
