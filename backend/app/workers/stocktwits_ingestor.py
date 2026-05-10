"""
StockTwits retail-sentiment ingestor — no API key required.

Fetches the public stream endpoint used by the StockTwits frontend:
  https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json

Strategy:
  1. Pull top-20 trending symbols from Redis (trending:global).
  2. Then iterate over all tickers in the DB (S&P 500 seed), skipping any
     already covered in step 1.
  3. Request each symbol sequentially with a 2-second sleep between calls to
     respect StockTwits' undocumented rate limit.
  4. Map the native StockTwits sentiment tag directly to ±1.0, bypassing
     TextBlob; fall back to TextBlob only when the tag is absent.

Deduplication: messages are keyed on stocktwits-<message_id> stored as url.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.embeddings import encode
from app.utils.minhash_dedup import compute_minhash, is_near_duplicate, load_recent_signatures
from app.utils.sentiment import analyze_sentiment

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
_TIMEOUT = 15
_SLEEP_BETWEEN_REQUESTS = 2.0   # seconds
_MAX_MESSAGES_PER_SYMBOL = 30   # StockTwits returns up to 30 per page

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stocktwits.com/",
}

_SENTIMENT_MAP = {
    "Bullish": 1.0,
    "Bearish": -1.0,
}


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse StockTwits ISO-8601 timestamp to UTC datetime."""
    try:
        # StockTwits format: "2024-05-01T12:00:00Z"
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _normalize(msg: dict, symbol: str) -> NormalizedItem | None:
    """Convert a raw StockTwits message dict to a NormalizedItem."""
    msg_id = msg.get("id")
    body = (msg.get("body") or "").strip()
    if not msg_id or not body:
        return None

    created_at = _parse_timestamp(msg.get("created_at", ""))

    # Native sentiment tag takes priority over TextBlob
    sentiment_tag = None
    entities = msg.get("entities") or {}
    sentiment_obj = entities.get("sentiment") or msg.get("entities", {})
    # StockTwits places sentiment under message.entities.sentiment.basic
    # e.g. {"basic": "Bullish"} or None
    if isinstance(sentiment_obj, dict):
        basic = sentiment_obj.get("basic")
        if basic in _SENTIMENT_MAP:
            sentiment_tag = _SENTIMENT_MAP[basic]

    sentiment = sentiment_tag if sentiment_tag is not None else analyze_sentiment(body)

    url = f"https://stocktwits.com/message/{msg_id}"

    return NormalizedItem(
        id=f"stocktwits-{msg_id}",
        source="stocktwits",
        title=None,
        content=body,
        timestamp=created_at,
        url=url,
        tickers=[symbol],
        sentiment=sentiment,
    )


async def _get_trending_symbols() -> list[str]:
    """Pull top-20 symbols from Redis trending:global. Returns [] on failure."""
    try:
        r = aioredis.from_url(
            settings.redis_url,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        raw = await r.get("trending:global")
        await r.aclose()
        if not raw:
            return []
        data = json.loads(raw)
        return [entry["symbol"] for entry in data[:20] if "symbol" in entry]
    except Exception as exc:
        logger.debug("stocktwits_ingestor: could not read Redis trending: %s", exc)
        return []


async def _fetch_stream(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    """Fetch raw messages for *symbol*. Returns [] on any error."""
    url = _BASE_URL.format(symbol=symbol)
    try:
        resp = await client.get(url, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get("messages", [])
        if resp.status_code == 404:
            logger.debug("stocktwits_ingestor: 404 for %s (not found)", symbol)
        else:
            logger.debug("stocktwits_ingestor: HTTP %d for %s", resp.status_code, symbol)
        return []
    except httpx.RequestError as exc:
        logger.debug("stocktwits_ingestor: request error for %s: %s", symbol, exc)
        return []


async def run(db: AsyncSession) -> list[NormalizedItem]:
    # 1. Top-20 trending from Redis
    trending_symbols = await _get_trending_symbols()

    # 2. All DB tickers (full S&P 500 seed)
    rows = await db.execute(select(Ticker.symbol))
    db_symbols: list[str] = [r[0] for r in rows]

    if not db_symbols and not trending_symbols:
        logger.info("stocktwits_ingestor: no tickers available — skipping")
        return []

    # Trending first, then remaining DB tickers (deduped, order preserved)
    seen_order: set[str] = set()
    ordered_symbols: list[str] = []
    for sym in trending_symbols + db_symbols:
        if sym not in seen_order:
            seen_order.add(sym)
            ordered_symbols.append(sym)

    logger.info(
        "stocktwits_ingestor: queuing %d symbols (%d trending + %d db-only)",
        len(ordered_symbols),
        len(trending_symbols),
        len(ordered_symbols) - len(trending_symbols),
    )

    # Pre-load existing StockTwits URLs to avoid redundant DB queries per message
    existing_rows = await db.execute(
        select(Article.url).where(Article.url.like("https://stocktwits.com/message/%"))
    )
    existing_urls: set[str] = {r[0] for r in existing_rows}
    seen_this_run: set[str] = set()
    recent_signatures = await load_recent_signatures(db)
    seen_sigs: list[list[int]] = list(recent_signatures)

    results: list[NormalizedItem] = []

    async with httpx.AsyncClient(headers=_HEADERS) as client:
        for i, symbol in enumerate(ordered_symbols):
            if i > 0:
                await asyncio.sleep(_SLEEP_BETWEEN_REQUESTS)

            raw_messages = await _fetch_stream(client, symbol)
            if not raw_messages:
                continue

            for msg in raw_messages[:_MAX_MESSAGES_PER_SYMBOL]:
                item = _normalize(msg, symbol)
                if item is None:
                    continue

                url = item.url or ""
                if not url or url in existing_urls or url in seen_this_run:
                    continue
                seen_this_run.add(url)

                sig = compute_minhash(item.content)
                if is_near_duplicate(sig, seen_sigs):
                    continue
                seen_sigs.append(sig)

                article = Article(
                    source=item.source,
                    title=item.title,
                    content=item.content,
                    url=item.url,
                    timestamp=item.timestamp,
                    minhash_signature=sig,
                    embedding=await encode(item.content),
                )
                db.add(article)
                await db.flush()

                db.add(
                    TickerMention(
                        ticker=symbol,
                        article_id=article.id,
                        sentiment=item.sentiment,
                        source_weight=0.3,
                    )
                )
                results.append(item)

    await db.commit()
    logger.info(
        "stocktwits_ingestor: persisted %d new messages across %d symbols",
        len(results),
        len(ordered_symbols),
    )
    return results


# ---------------------------------------------------------------------------
# Test block — run directly: python -m app.workers.stocktwits_ingestor
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio as _asyncio

    _TEST_SYMBOLS = ["AAPL", "SPY"]

    async def _test() -> None:
        async with httpx.AsyncClient(headers=_HEADERS) as client:
            for sym in _TEST_SYMBOLS:
                print(f"\n{'='*60}")
                print(f"Testing symbol: {sym}")
                print(f"{'='*60}")

                url = _BASE_URL.format(symbol=sym)
                try:
                    resp = await client.get(url, timeout=_TIMEOUT)
                    print(f"HTTP status : {resp.status_code}")

                    if resp.status_code != 200:
                        print("  [SKIP] non-200 response")
                        continue

                    data = resp.json()
                    print(f"Response keys: {list(data.keys())}")

                    messages = data.get("messages", [])
                    print(f"Messages returned: {len(messages)}")

                    for idx, msg in enumerate(messages[:3], 1):
                        item = _normalize(msg, sym)
                        if item is None:
                            continue
                        tag_raw = (
                            (msg.get("entities") or {})
                            .get("sentiment") or {}
                        )
                        print(
                            f"\n  [{idx}] id={msg.get('id')}  "
                            f"sentiment_tag={tag_raw.get('basic', 'n/a')}  "
                            f"score={item.sentiment:.3f}"
                        )
                        print(f"       {item.content[:120]}")

                except httpx.RequestError as exc:
                    print(f"  [ERROR] {exc}")

                if sym != _TEST_SYMBOLS[-1]:
                    await _asyncio.sleep(_SLEEP_BETWEEN_REQUESTS)

    _asyncio.run(_test())
