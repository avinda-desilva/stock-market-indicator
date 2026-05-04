#!/usr/bin/env python3
"""
Standalone runner for all ingestors.
Can be executed directly or called from a system cron job.

Usage (from repo root):
    python3 data-pipeline/run_ingestors.py [news|social|market|all]
"""
import asyncio
import glob
import logging
import sys
from pathlib import Path

# Always add /app first — this is the WORKDIR inside Docker where backend/
# is mounted, making `app/` importable. It is a no-op outside Docker.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

# Locally: inject the backend directory and the local venv site-packages.
_repo_root = Path(__file__).resolve().parent.parent
_backend = str(_repo_root / "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_venv_lib = _repo_root / "backend" / ".venv" / "lib"
if _venv_lib.exists():
    for _sp in glob.glob(str(_venv_lib / "python3*" / "site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

from app.database import AsyncSessionLocal  # noqa: E402
from app.workers import news_ingestor, social_ingestor, market_ingestor  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_WORKERS = {
    "news": news_ingestor,
    "social": social_ingestor,
    "market": market_ingestor,
}


async def run(target: str = "all") -> None:
    workers = _WORKERS if target == "all" else {target: _WORKERS[target]}
    for name, worker in workers.items():
        async with AsyncSessionLocal() as db:
            try:
                items = await worker.run(db)
                logger.info("%s: ingested %d items", name, len(items))
            except Exception as exc:
                logger.error("%s failed: %s", name, exc)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in (*_WORKERS, "all"):
        print(f"Usage: {sys.argv[0]} [{'|'.join(_WORKERS)}|all]")
        sys.exit(1)
    asyncio.run(run(target))
