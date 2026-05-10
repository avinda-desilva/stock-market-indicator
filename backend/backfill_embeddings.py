"""
One-shot backfill: generate and store vector embeddings for all articles
that currently have embedding = NULL.

Uses encode_batch() so the model processes 64 articles per inference call
instead of one at a time — roughly 64x faster than sequential encode().

Run inside the backend container:
  python backfill_embeddings.py

Progress is logged every batch. Safe to re-run: only NULL rows are touched.
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

BATCH_SIZE = 64  # matches encode_batch's internal batch_size — one model call per DB batch


async def backfill() -> None:
    raw_url = os.environ["DATABASE_URL"]
    async_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from app.models.article import Article
    from app.utils.embeddings import encode_batch

    async with SessionLocal() as db:
        total = (
            await db.execute(
                select(Article.id).where(Article.embedding.is_(None))
            )
        ).all()
        total_count = len(total)
        logger.info("Articles to backfill: %d", total_count)

        if total_count == 0:
            logger.info("Nothing to do.")
            await engine.dispose()
            return

        processed = 0

        while True:
            rows = (
                await db.execute(
                    select(Article.id, Article.title, Article.content)
                    .where(Article.embedding.is_(None))
                    .order_by(Article.id)
                    .limit(BATCH_SIZE)
                )
            ).all()

            if not rows:
                break

            texts = [
                f"{title or ''} {content or ''}".strip()
                for _, title, content in rows
            ]
            embeddings = await encode_batch(texts)

            for (article_id, _, _), embedding in zip(rows, embeddings):
                await db.execute(
                    update(Article)
                    .where(Article.id == article_id)
                    .values(embedding=embedding)
                )

            await db.commit()
            processed += len(rows)
            logger.info(
                "Backfilled %d / %d articles (%.1f%%)",
                processed,
                total_count,
                processed / total_count * 100,
            )

    await engine.dispose()
    logger.info("Backfill complete — %d articles updated", processed)


if __name__ == "__main__":
    asyncio.run(backfill())
