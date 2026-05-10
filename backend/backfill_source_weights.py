"""
Backfill source_weight on existing ticker_mentions rows.

Runs a single UPDATE with a CASE expression joining ticker_mentions → articles
so each row gets the correct weight based on article.source.

Source weight table:
    alphavantage      1.00
    polygon           0.90
    finnhub_general   0.90
    finnhub_company   0.90
    finnhub_insider   0.90
    yahoo_rss         0.85
    newsapi           0.80
    googlenews_rss    0.80
    gdelt             0.75
    stocktwits        0.30
    twitter           0.30
    reddit            0.20
    <unknown>         1.00  (safe default; will not silently zero-out anything)
"""

import asyncio
import logging
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Pulled from the same env var the app uses so this script works inside or
# outside Docker as long as DATABASE_URL is exported.
import os
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://smi_admin_user:smi_admin_password@localhost:5432/smi_protected_db",
)
# Ensure asyncpg driver is used regardless of how the URL was set in .env
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

WEIGHT_CASE = """
    CASE a.source
        WHEN 'alphavantage'    THEN 1.00
        WHEN 'polygon'         THEN 0.90
        WHEN 'finnhub_general' THEN 0.90
        WHEN 'finnhub_company' THEN 0.90
        WHEN 'finnhub_insider' THEN 0.90
        WHEN 'yahoo_rss'       THEN 0.85
        WHEN 'newsapi'         THEN 0.80
        WHEN 'googlenews_rss'  THEN 0.80
        WHEN 'gdelt'           THEN 0.75
        WHEN 'stocktwits'      THEN 0.30
        WHEN 'twitter'         THEN 0.30
        WHEN 'reddit'          THEN 0.20
        ELSE                        1.00
    END
"""

DRY_RUN_SQL = f"""
SELECT a.source, COUNT(*) AS mention_count,
       ({WEIGHT_CASE}) AS new_weight
FROM ticker_mentions tm
JOIN articles a ON a.id = tm.article_id
WHERE tm.source_weight = 1.0
GROUP BY a.source, new_weight
ORDER BY mention_count DESC;
"""

UPDATE_SQL = f"""
UPDATE ticker_mentions tm
SET    source_weight = (
    {WEIGHT_CASE}
)
FROM articles a
WHERE a.id = tm.article_id
  AND tm.source_weight = 1.0;
"""


async def run(dry_run: bool = False) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        if dry_run:
            logger.info("DRY RUN — rows that would be updated:")
            result = await conn.execute(text(DRY_RUN_SQL))
            rows = result.fetchall()
            if not rows:
                logger.info("  (none — all rows already have non-default weights)")
            for row in rows:
                logger.info("  source=%-20s mentions=%5d  → weight=%.2f", *row)
            return

        logger.info("Backfilling source_weight on ticker_mentions …")
        result = await conn.execute(text(UPDATE_SQL))
        logger.info("Done — %d rows updated.", result.rowcount)

    await engine.dispose()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run(dry_run=dry))
