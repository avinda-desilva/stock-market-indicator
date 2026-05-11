"""
Yahoo Finance RSS ingestor — no API key required.

Yahoo publishes a public RSS feed per ticker:
  https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}&region=US&lang=en-US

Strategy: iterate over all tickers in the DB, fetch each feed, parse with
stdlib xml.etree (no extra dependency). To avoid hammering Yahoo we cap
concurrent HTTP requests at 5.

Volume: 50 tickers × ~10 items/feed = ~500 articles per run, minus duplicates.
LLM analysis is throttled by the shared Ollama semaphore in llm_analyzer — with
OLLAMA_MAX_CONCURRENCY=1 the calls drain sequentially, preventing OOM on the
Ollama container regardless of batch size.
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
from app.utils.embeddings import encode
from app.utils.llm_analyzer import analyze_article
from app.utils.minhash_dedup import compute_minhash, is_near_duplicate, load_recent_signatures

logger = logging.getLogger(__name__)

_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
_HTTP_CONCURRENCY = 5     # max simultaneous HTTP fetches (unrelated to Ollama)
_TIMEOUT = 10             # seconds per HTTP request
_MAX_ITEMS_PER_FEED = 15  # cap per ticker to keep DB growth reasonable


def _parse_rss_date(date_str: str) -> datetime:
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _parse_feed(xml_bytes: bytes, symbol: str) -> list[NormalizedItem]:
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

        items.append(
            NormalizedItem(
                id=f"yahoo-{symbol}-{url}",
                source="yahoo_rss",
                title=title or None,
                content=description,
                timestamp=timestamp,
                url=url,
                tickers=[symbol],
                sentiment=None,  # filled by LLM below
            )
        )

    return items


async def _fetch_feed(client: httpx.AsyncClient, symbol: str) -> tuple[str, bytes | None]:
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
    rows = await db.execute(select(Ticker.symbol))
    symbols = [r[0] for r in rows]
    if not symbols:
        logger.info("yahoo_rss_ingestor: no tickers in DB — skipping")
        return []

    # Fetch all RSS feeds concurrently, capped at _HTTP_CONCURRENCY.
    # This semaphore controls network I/O only — the Ollama semaphore in
    # llm_analyzer handles LLM concurrency separately.
    http_sem = asyncio.Semaphore(_HTTP_CONCURRENCY)

    async def _guarded_fetch(client: httpx.AsyncClient, sym: str):
        async with http_sem:
            return await _fetch_feed(client, sym)

    async with httpx.AsyncClient(headers={"User-Agent": "stock-market-indicator/1.0"}) as client:
        tasks = [_guarded_fetch(client, sym) for sym in symbols]
        feed_results: list[tuple[str, bytes | None]] = await asyncio.gather(*tasks)

    candidates: list[NormalizedItem] = []
    for symbol, xml_bytes in feed_results:
        if xml_bytes:
            candidates.extend(_parse_feed(xml_bytes, symbol))

    if not candidates:
        logger.info("yahoo_rss_ingestor: no items parsed from any feed")
        return []

    # Bulk URL dedup against DB
    candidate_urls = list({c.url for c in candidates if c.url})
    existing_rows = await db.execute(
        select(Article.url).where(Article.url.in_(candidate_urls))
    )
    existing_urls: set[str] = {r[0] for r in existing_rows}

    recent_signatures = await load_recent_signatures(db)
    seen_this_run: set[str] = set()
    seen_sigs: list[list[int]] = list(recent_signatures)
    results: list[NormalizedItem] = []

    for item in candidates:
        url = item.url or ""
        if not url or url in existing_urls or url in seen_this_run:
            continue
        seen_this_run.add(url)

        sig = compute_minhash(item.content)
        if is_near_duplicate(sig, seen_sigs):
            continue
        seen_sigs.append(sig)

        # item.tickers[0] is always set by _parse_feed (the feed's own symbol)
        ticker = item.tickers[0]
        article_text = f"{item.title or ''}\n\n{item.content}"

        # LLM: confirm relevance + get per-ticker sentiment.
        # Semaphore inside analyze_article caps Ollama concurrency globally.
        analysis = await analyze_article(ticker, article_text)
        if analysis is None:
            # LLM judged the article irrelevant to this ticker — skip.
            continue

        item.sentiment = analysis.sentiment

        article = Article(
            source=item.source,
            title=item.title,
            content=item.content,
            url=item.url,
            timestamp=item.timestamp,
            minhash_signature=sig,
            embedding=await encode(article_text),
        )
        db.add(article)
        await db.flush()

        db.add(
            TickerMention(
                ticker=ticker,
                article_id=article.id,
                sentiment=analysis.sentiment,
                source_weight=0.85,
                llm_summary=analysis.summary,
            )
        )
        results.append(item)

    await db.commit()
    logger.info(
        "yahoo_rss_ingestor: persisted %d new articles across %d tickers",
        len(results), len(symbols),
    )
    return results
