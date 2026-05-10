"""
Google News RSS ingestor — no API key required.

Fetches articles by iterating over 15 canonical boolean sector search queries.
Base URL: https://news.google.com/rss/search?q={URL_ENCODED_QUERY}&hl=en-US&gl=US&ceid=US:en

Strategy: fire all 15 queries concurrently (semaphore-capped), parse the
Atom-flavored RSS that Google returns, extract title + source + pubDate,
run TextBlob sentiment and DB-backed ticker extraction, then deduplicate
against existing Article.url values before persisting.

Volume: 15 queries × ~10 items/feed ≈ 150 articles per run, minus duplicates.
"""

import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.article import Article
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.embeddings import encode
from app.utils.minhash_dedup import compute_minhash, is_near_duplicate, load_recent_signatures
from app.utils.sentiment import analyze_sentiment
from app.utils.ticker_extractor import extract_tickers_db

logger = logging.getLogger(__name__)

_BASE_URL = "https://news.google.com/rss/search"
_CONCURRENCY = 5          # max simultaneous HTTP requests
_TIMEOUT = 15             # seconds per request
_MAX_ITEMS_PER_FEED = 15  # cap per query to keep DB growth reasonable

# Google News RSS uses Atom namespace for <source> publisher tag
_ATOM_NS = "http://www.w3.org/2005/Atom"

SECTOR_QUERIES: list[str] = [
    # Tech
    '"big tech" AND ("earnings" OR "stock" OR "revenue")',
    '("semiconductor" OR "microchip") AND ("stock" OR "market")',
    '"cloud computing" AND ("growth" OR "market share")',
    # AI
    '"artificial intelligence" AND ("stock" OR "IPO" OR "market")',
    '"generative AI" AND ("enterprise" OR "investment")',
    '("machine learning" OR "AI") AND "regulation" AND "markets"',
    # Fin-Tech
    '"fintech" AND ("earnings" OR "disruption" OR "stock")',
    '("crypto" OR "bitcoin") AND ("ETF" OR "regulation" OR "markets")',
    '"digital payments" AND ("revenue" OR "stock")',
    # Finance
    '"federal reserve" AND ("interest rates" OR "inflation") AND "markets"',
    '"stock market" AND ("correction" OR "rally" OR "crash")',
    '("CPI" OR "inflation") AND "economic data" AND "markets"',
    # World News Sentiment
    '"geopolitics" AND ("supply chain" OR "tariffs" OR "markets")',
    '"OPEC" AND ("oil prices" OR "energy sector")',
    '"global economy" AND ("recession" OR "growth" OR "outlook")',
]


_OUTLET_SUFFIX_RE = re.compile(r"\s+-\s+[^-]+$")


def _dedup_text(title: str) -> str:
    """
    Return a normalised, outlet-stripped title suitable for MinHash.

    Google News appends " - Publisher Name" to every title, so two syndicated
    copies of the same story differ only in that suffix.  Stripping it gives
    MinHash a stable, content-based fingerprint across outlets.
    """
    stripped = _OUTLET_SUFFIX_RE.sub("", title).strip()
    return stripped or title


def _parse_rss_date(date_str: str) -> datetime:
    """Parse RFC-2822 pubDate from Google News RSS; fall back to now on failure."""
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _stable_id(query: str, url: str) -> str:
    """Deterministic article ID — short hex of query + url."""
    digest = hashlib.md5(f"{query}||{url}".encode()).hexdigest()[:12]
    return f"googlenews-{digest}"


def _parse_feed(xml_bytes: bytes, query: str) -> list[dict]:
    """
    Parse a Google News RSS response and return raw item dicts.

    Returns list of dicts with keys: title, url, description, pub_date, source_name.
    Ticker extraction is async, so it's deferred to the caller.

    Google News RSS structure:
      <rss>
        <channel>
          <item>
            <title>…</title>
            <link>…</link>          ← canonical article URL
            <pubDate>…</pubDate>
            <description>…</description>
            <source url="…">Publisher Name</source>
          </item>
        </channel>
      </rss>
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        logger.debug("googlenews_ingestor: XML parse error for query %r: %s", query[:40], exc)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    raw_items: list[dict] = []
    for i, item_el in enumerate(channel.findall("item")):
        if i >= _MAX_ITEMS_PER_FEED:
            break

        title = (item_el.findtext("title") or "").strip()
        url = (item_el.findtext("link") or "").strip()
        description = (item_el.findtext("description") or title).strip()
        pub_date = (item_el.findtext("pubDate") or "").strip()

        # Publisher name lives in <source>text</source>
        source_el = item_el.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""

        if not url:
            continue

        raw_items.append(
            {
                "title": title,
                "url": url,
                "description": description,
                "pub_date": pub_date,
                "source_name": source_name,
            }
        )

    return raw_items


async def _fetch_feed(client: httpx.AsyncClient, query: str) -> tuple[str, bytes | None]:
    """Fetch RSS XML for a single query string. Returns (query, bytes) or (query, None)."""
    encoded_q = quote(query, safe="")
    url = f"{_BASE_URL}?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = await client.get(url, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return query, resp.content
        logger.debug("googlenews_ingestor: HTTP %d for query %r", resp.status_code, query[:40])
        return query, None
    except httpx.RequestError as exc:
        logger.debug("googlenews_ingestor: request error for query %r: %s", query[:40], exc)
        return query, None


async def run(db: AsyncSession) -> list[NormalizedItem]:
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _guarded_fetch(client: httpx.AsyncClient, q: str):
        async with semaphore:
            return await _fetch_feed(client, q)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; stock-market-indicator/1.0; "
            "+https://github.com/stock-market-indicator)"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        tasks = [_guarded_fetch(client, q) for q in SECTOR_QUERIES]
        feed_results: list[tuple[str, bytes | None]] = await asyncio.gather(*tasks)

    # Parse all feeds — collect (query, raw_item) pairs
    raw_pairs: list[tuple[str, dict]] = []
    for query, xml_bytes in feed_results:
        if xml_bytes:
            for raw in _parse_feed(xml_bytes, query):
                raw_pairs.append((query, raw))

    if not raw_pairs:
        logger.info("googlenews_ingestor: no items parsed from any query")
        return []

    # Batch-deduplicate against DB in one query
    candidate_urls = list({raw["url"] for _, raw in raw_pairs})
    existing_rows = await db.execute(
        select(Article.url).where(Article.url.in_(candidate_urls))
    )
    existing_urls: set[str] = {r[0] for r in existing_rows}

    recent_signatures = await load_recent_signatures(db)
    seen_this_run: set[str] = set()
    seen_sigs: list[list[int]] = list(recent_signatures)
    results: list[NormalizedItem] = []

    for query, raw in raw_pairs:
        url = raw["url"]
        if not url or url in existing_urls or url in seen_this_run:
            continue
        seen_this_run.add(url)

        title = raw["title"]
        description = raw["description"]
        pub_date = raw["pub_date"]
        source_name = raw["source_name"] or "googlenews"

        timestamp = _parse_rss_date(pub_date) if pub_date else datetime.now(timezone.utc)
        text = f"{title} {description}"
        sentiment = analyze_sentiment(text)

        sig = compute_minhash(_dedup_text(title))
        if is_near_duplicate(sig, seen_sigs):
            continue
        seen_sigs.append(sig)

        # DB-backed ticker extraction (symbol + company-name matching)
        tickers = await extract_tickers_db(text, db)

        item = NormalizedItem(
            id=_stable_id(query, url),
            source="googlenews_rss",
            title=title or None,
            content=description,
            timestamp=timestamp,
            url=url,
            tickers=tickers,
            sentiment=sentiment,
        )

        article = Article(
            source=item.source,
            title=item.title,
            content=item.content,
            url=item.url,
            timestamp=item.timestamp,
            minhash_signature=sig,
            embedding=await encode(f"{item.title or ''} {item.content}"),
        )
        db.add(article)
        await db.flush()

        for symbol in item.tickers:
            db.add(
                TickerMention(
                    ticker=symbol,
                    article_id=article.id,
                    sentiment=item.sentiment,
                    source_weight=0.8,
                )
            )

        results.append(item)

    await db.commit()
    logger.info(
        "googlenews_ingestor: persisted %d new articles from %d queries",
        len(results),
        len(SECTOR_QUERIES),
    )
    return results


# ---------------------------------------------------------------------------
# Testing block — run directly:  python -m app.workers.googlenews_ingestor
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio
    import xml.etree.ElementTree as ET
    from urllib.parse import quote

    import httpx

    TEST_QUERY = '"big tech" AND ("earnings" OR "stock" OR "revenue")'

    async def _test():
        encoded_q = quote(TEST_QUERY, safe="")
        url = f"{_BASE_URL}?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"
        print(f"Fetching: {url}\n")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; stock-market-indicator/1.0; "
                "+https://github.com/stock-market-indicator)"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            resp = await client.get(url, timeout=_TIMEOUT)

        print(f"HTTP status: {resp.status_code}")
        if resp.status_code != 200:
            print("Non-200 response — aborting test.")
            return

        raw_items = _parse_feed(resp.content, TEST_QUERY)
        print(f"Parsed {len(raw_items)} item(s). Showing first 5:\n")

        for i, item in enumerate(raw_items[:5], 1):
            timestamp = _parse_rss_date(item["pub_date"]) if item["pub_date"] else "N/A"
            sentiment = analyze_sentiment(f"{item['title']} {item['description']}")
            print(f"[{i}] title      : {item['title']}")
            print(f"     url        : {item['url']}")
            print(f"     source     : {item['source_name']}")
            print(f"     pub_date   : {item['pub_date']}")
            print(f"     timestamp  : {timestamp}")
            print(f"     sentiment  : {sentiment:.4f}")
            print(f"     description: {item['description'][:120]}...")
            print()

    asyncio.run(_test())
