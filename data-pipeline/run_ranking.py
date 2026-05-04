#!/usr/bin/env python3
"""
Standalone ranking-engine runner.

Runs a single scoring cycle: reads ticker mentions from PostgreSQL,
computes scores, applies spike detection, and writes results to Redis.

Usage (from repo root):
    python3 data-pipeline/run_ranking.py

Cron every minute:
    * * * * *  cd /path/to/stock-market-indicator && python3 data-pipeline/run_ranking.py >> /var/log/smi/ranking.log 2>&1
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

from app.workers.ranking_engine import run_ranking  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    asyncio.run(run_ranking())
