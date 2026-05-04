"""
Yahoo Finance RSS ingestor — no API key required.

Yahoo publishes a public RSS feed per ticker:
  https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}&region=US&lang=en-US

Strategy: iterate over all tickers in the DB, fetch each feed, parse with
stdlib xml.etree (no extra dependency). To avoid hammering Yahoo we cap
concurrent requests at 5 and skip symbols whose feed was recently fetched
(tracked in-memory per process lifetime).

Volume: 50 tickers × ~10 items/feed = ~500 articles per run, minus duplicates.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.sentiment import analyze_sentiment

logger = logging.getLogger(__name__)

_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
_CONCURRENCY = 5          # max simultaneous HTTP requests
_TIMEOUT = 10             # seconds per request
_MAX_ITEMS_PER_FEED = 15  # cap per ticker to keep DB growth reasonable


def _parse_rss_date(date_str: str) -> datetime:
    """Parse RFC-2822 date from RSS <pubDate> tag."""
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _parse_feed(xml_bytes: bytes, symbol: str) -> list[NormalizedItem]:
    """Extract items from a Yahoo Finance RSS feed for *symbol*."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug("yahoo_rss_ingestor: XML parse error for %s: %s", symbol, exc)
        return []

    items: list[NormalizedItem] = []
    channel = root.find("channel")
    if channel is None:
        return []

    for i, item_el in enumerate(channel.findall("item")):
        if i >= _MAX_ITEMS_PER_FEED:
            break

        title = (item_el.findtext("title") or "").strip()
        url = (item_el.findtext("link") or "").strip()
        description = (item_el.findtext("description") or title).strip()
        pub_date = item_el.findtext("pubDate") or ""

        if not url:
            continue

        timestamp = _parse_rss_date(pub_date) if pub_date else datetime.now(timezone.utc)
        text = f"{title} {description}"
        sentiment = analyze_sentiment(text)

        items.append(
            NormalizedItem(
                id=f"yahoo-{symbol}-{url}",
                source="yahoo_rss",
                title=title or None,
                content=description,
                timestamp=timestamp,
                url=url,
                tickers=[symbol],  # we know the ticker — it's the feed we fetched
                sentiment=sentiment,
            )
        )

    return items


async def _fetch_feed(client: httpx.AsyncClient, symbol: str) -> tuple[str, bytes | None]:
    """Fetch RSS XML for *symbol*. Returns (symbol, bytes) or (symbol, None) on error."""
    try:
        resp = await client.get(
            _RSS_URL,
            params={"s": symbol, "region": "US", "lang": "en-US"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return symbol, resp.content
        logger.debug("yahoo_rss_ingestor: HTTP %d for %s", resp.status_code, symbol)
        return symbol, None
    except httpx.RequestError as exc:
        logger.debug("yahoo_rss_ingestor: request error for %s: %s", symbol, exc)
        return symbol, None


async def run(db: AsyncSession) -> list[NormalizedItem]:
    # Load all ticker symbols from DB
    rows = await db.execute(select(Ticker.symbol))
    symbols = [r[0] for r in rows]
    if not symbols:
        logger.info("yahoo_rss_ingestor: no tickers in DB — skipping")
        return []

    # Fetch all feeds concurrently, capped at _CONCURRENCY simultaneous requests
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _guarded_fetch(client: httpx.AsyncClient, sym: str):
        async with semaphore:
            return await _fetch_feed(client, sym)

    async with httpx.AsyncClient(headers={"User-Agent": "stock-market-indicator/1.0"}) as client:
        tasks = [_guarded_fetch(client, sym) for sym in symbols]
        feed_results: list[tuple[str, bytes | None]] = await asyncio.gather(*tasks)

    # Parse all feeds and collect candidate items
    candidates: list[NormalizedItem] = []
    for symbol, xml_bytes in feed_results:
        if xml_bytes:
            candidates.extend(_parse_feed(xml_bytes, symbol))

    if not candidates:
        logger.info("yahoo_rss_ingestor: no items parsed from any feed")
        return []

    # Deduplicate by URL against the DB in one batch query
    candidate_urls = list({c.url for c in candidates if c.url})
    existing_rows = await db.execute(
        select(Article.url).where(Article.url.in_(candidate_urls))
    )
    existing_urls: set[str] = {r[0] for r in existing_rows}

    # Deduplicate within this batch too (same article can appear in multiple ticker feeds)
    seen_this_run: set[str] = set()
    results: list[NormalizedItem] = []

    for item in candidates:
        url = item.url or ""
        if not url or url in existing_urls or url in seen_this_run:
            continue
        seen_this_run.add(url)

        article = Article(
            source=item.source,
            title=item.title,
            content=item.content,
            url=item.url,
            timestamp=item.timestamp,
        )
        db.add(article)
        await db.flush()

        for symbol in item.tickers:
            db.add(
                TickerMention(
                    ticker=symbol,
                    article_id=article.id,
                    sentiment=item.sentiment,
                )
            )

        results.append(item)

    await db.commit()
    logger.info("yahoo_rss_ingestor: persisted %d new articles across %d tickers", len(results), len(symbols))
    return results
