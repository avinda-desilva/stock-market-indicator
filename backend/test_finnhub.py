"""
Finnhub endpoint verification script.
Run from the backend/ directory (no Docker needed — uses httpx directly):

    python test_finnhub.py

Checks:
  1. General Market News   — expects HTTP 200, prints first 2 normalized items
  2. Company News (AAPL)   — expects HTTP 200, prints first 2 normalized items
  3. Insider Sentiment (AAPL) — expects HTTP 200, prints first 2 normalized items

Prints HTTP status for each call so 403 (bad key) and 429 (rate-limit) are
immediately visible.
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# ── Load .env from project root (one level up from backend/) ─────────────────
_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")

API_KEY = os.getenv("FINNHUB_API_KEY", "")
BASE = "https://finnhub.io/api/v1"

if not API_KEY:
    print("ERROR: FINNHUB_API_KEY is not set in .env — aborting.")
    sys.exit(1)

HEADERS = {"X-Finnhub-Token": API_KEY}  # alternative auth (also works alongside ?token=)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(unix) -> str:
    try:
        return datetime.fromtimestamp(float(unix), tz=timezone.utc).isoformat()
    except Exception:
        return "n/a"


def _date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


def _print_item(idx: int, item: dict) -> None:
    print(f"\n  [{idx}]")
    for k, v in item.items():
        val = str(v)
        print(f"    {k:<22} {val[:90]}")


# ── Test 1: General Market News ───────────────────────────────────────────────

async def test_general_news(client: httpx.AsyncClient) -> None:
    _print_section("Endpoint 1 — General Market News  GET /news?category=general")

    resp = await client.get(
        f"{BASE}/news",
        params={"category": "general", "token": API_KEY},
        timeout=15,
    )
    print(f"\n  HTTP Status : {resp.status_code}")

    if resp.status_code != 200:
        print(f"  Body        : {resp.text[:300]}")
        return

    data = resp.json()
    items = data if isinstance(data, list) else []
    print(f"  Items returned : {len(items)}")

    for i, raw in enumerate(items[:2], 1):
        normalized = {
            "source":    "finnhub_general",
            "title":     (raw.get("headline") or "")[:80],
            "timestamp": _ts(raw.get("datetime")),
            "url":       (raw.get("url") or "")[:80],
            "related":   raw.get("related") or "(none)",
            "sentiment": "→ TextBlob on title+summary",
        }
        _print_item(i, normalized)


# ── Test 2: Company News ──────────────────────────────────────────────────────

async def test_company_news(client: httpx.AsyncClient) -> None:
    symbol = "AAPL"
    today = date.today()
    from_date = _date(today - timedelta(days=2))
    to_date = _date(today)

    _print_section(
        f"Endpoint 2 — Company News  GET /company-news?symbol={symbol}"
        f"&from={from_date}&to={to_date}"
    )

    resp = await client.get(
        f"{BASE}/company-news",
        params={"symbol": symbol, "from": from_date, "to": to_date, "token": API_KEY},
        timeout=15,
    )
    print(f"\n  HTTP Status : {resp.status_code}")

    if resp.status_code != 200:
        print(f"  Body        : {resp.text[:300]}")
        return

    data = resp.json()
    items = data if isinstance(data, list) else []
    print(f"  Items returned : {len(items)}")

    for i, raw in enumerate(items[:2], 1):
        normalized = {
            "source":    "finnhub_company",
            "ticker":    symbol,
            "title":     (raw.get("headline") or "")[:80],
            "timestamp": _ts(raw.get("datetime")),
            "url":       (raw.get("url") or "")[:80],
            "sentiment": "→ TextBlob on title+summary",
        }
        _print_item(i, normalized)


# ── Test 3: Insider Sentiment ─────────────────────────────────────────────────

async def test_insider_sentiment(client: httpx.AsyncClient) -> None:
    symbol = "AAPL"
    today = date.today()
    from_date = _date(today.replace(day=1) - timedelta(days=90))
    to_date = _date(today)

    _print_section(
        f"Endpoint 3 — Insider Sentiment  GET /stock/insider-sentiment?symbol={symbol}"
        f"&from={from_date}&to={to_date}"
    )

    resp = await client.get(
        f"{BASE}/stock/insider-sentiment",
        params={"symbol": symbol, "from": from_date, "to": to_date, "token": API_KEY},
        timeout=15,
    )
    print(f"\n  HTTP Status : {resp.status_code}")

    if resp.status_code != 200:
        print(f"  Body        : {resp.text[:300]}")
        return

    data = resp.json()
    records = data.get("data", []) if isinstance(data, dict) else []
    print(f"  Records returned : {len(records)}")

    for i, raw in enumerate(records[:2], 1):
        mspr = raw.get("mspr", 0.0)
        change = raw.get("change", 0)
        year = raw.get("year", "?")
        month = raw.get("month", "?")
        direction = "buying" if float(mspr) >= 0 else "selling"

        normalized = {
            "source":    "finnhub_insider",
            "ticker":    symbol,
            "period":    f"{year}-{month:02d}" if isinstance(month, int) else f"{year}-{month}",
            "mspr":      f"{mspr:+.4f}  (sentiment → {float(mspr):+.4f})",
            "change":    f"{change:,} net shares  ({direction})",
            "url":       f"finnhub://insider-sentiment/{symbol}/{year}-{month}",
        }
        _print_item(i, normalized)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\nFinnhub API key loaded : {'*' * (len(API_KEY) - 4)}{API_KEY[-4:]}")
    print(f"Testing against        : {BASE}")

    async with httpx.AsyncClient(headers=HEADERS) as client:
        await test_general_news(client)
        await asyncio.sleep(1)   # polite gap between calls
        await test_company_news(client)
        await asyncio.sleep(1)
        await test_insider_sentiment(client)

    print("\n\nDone. All three endpoints tested.")
    print("Expected: HTTP 200 on all. If you see 403 → key invalid. 429 → rate-limit hit.")


if __name__ == "__main__":
    asyncio.run(main())
