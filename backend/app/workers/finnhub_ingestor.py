"""
Finnhub ingestor — three free-tier endpoints for maximum sentiment density.

Endpoints used (all free tier, no paid plan required):
  1. General Market News   GET /news?category=general
     → broad financial headlines, ~100 items/call, no ticker loop.
     → Schedule: every 30 min.

  2. Company News          GET /company-news?symbol=&from=&to=
     → per-ticker articles with the ticker already known — skips NLP.
     → Iterates over all DB tickers. A token-bucket throttle caps
       throughput at 55 calls/min (leaves 5-call headroom under the
       free-tier limit of 60 calls/min).
     → Schedule: every 2 hours.

  3. Insider Sentiment     GET /stock/insider-sentiment?symbol=&from=&to=
     → Monthly Share Purchase Ratio (MSPR) & net change for each ticker.
       MSPR in [-1, 1]: positive = net buying (bullish), negative = net
       selling (bearish). Stored as a synthetic article so it flows through
       the standard ranking pipeline.
     → Also token-bucket throttled.
     → Schedule: every 6 hours.

Rate-limiting strategy — token-bucket:
  Capacity: 55 tokens (conservative headroom under the 60 req/min limit).
  Refill:   55 tokens per 60 seconds (one full bucket per minute).
  Each HTTP call consumes one token. If the bucket is empty the coroutine
  awaits until refill, ensuring we never burst past the free-tier cap.
"""

import asyncio
import logging
import time
from datetime import datetime, date, timedelta, timezone

import httpx
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
from app.utils.ticker_extractor import extract_tickers_db

logger = logging.getLogger(__name__)

# ── Finnhub base URL ─────────────────────────────────────────────────────────
_BASE = "https://finnhub.io/api/v1"

# ── Token-bucket parameters ───────────────────────────────────────────────────
_BUCKET_CAPACITY: int = 55       # tokens (calls) we allow per window
_BUCKET_REFILL_RATE: float = 55  # tokens restored per _WINDOW_SECONDS
_WINDOW_SECONDS: float = 60.0    # rolling window size

# ── Company-news window ───────────────────────────────────────────────────────
_COMPANY_NEWS_DAYS_BACK: int = 2   # fetch last 2 days of company news per run
_MAX_TICKERS_PER_RUN: int = 50     # hard cap — avoids very long runs on large ticker tables

# ── Insider-sentiment window ─────────────────────────────────────────────────
_INSIDER_MONTHS_BACK: int = 3


# ── Minimal async token-bucket ────────────────────────────────────────────────

class _TokenBucket:
    """
    Leaky-bucket rate limiter for async code.
    Allows up to `capacity` calls per `window` seconds.
    """

    def __init__(self, capacity: int, window: float) -> None:
        self._capacity = capacity
        self._tokens = float(capacity)
        self._window = window
        self._refill_rate = capacity / window  # tokens/second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        async with self._lock:
            self._refill()
            while self._tokens < 1.0:
                # Calculate how long until the next token arrives
                wait = (1.0 - self._tokens) / self._refill_rate
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self._capacity),
            self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now


# Module-level bucket shared across all calls within a single run
_bucket = _TokenBucket(_BUCKET_CAPACITY, _WINDOW_SECONDS)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts_from_unix(unix: int | float | None) -> datetime:
    if unix is None:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromtimestamp(float(unix), tz=timezone.utc)
    except (OSError, ValueError, OverflowError):
        return datetime.now(timezone.utc)


def _date_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


async def _existing_urls(db: AsyncSession, urls: list[str]) -> set[str]:
    if not urls:
        return set()
    rows = await db.execute(select(Article.url).where(Article.url.in_(urls)))
    return {r[0] for r in rows}


async def _load_symbols(db: AsyncSession, limit: int | None = None) -> list[str]:
    stmt = select(Ticker.symbol)
    if limit:
        stmt = stmt.limit(limit)
    rows = await db.execute(stmt)
    return [r[0] for r in rows]


async def _persist(
    db: AsyncSession,
    item: NormalizedItem,
    seen_urls: set[str],
    existing_urls_set: set[str],
    seen_sigs: list[list[int]],
    per_ticker_sentiment: dict[str, float] | None = None,
) -> bool:
    """
    Persist one NormalizedItem if its URL hasn't been seen and content is not a near-duplicate.
    Returns True when a new article is written.
    `per_ticker_sentiment` maps symbol → float for fine-grained TickerMention scores.
    """
    url = item.url or ""
    if not url or url in seen_urls or url in existing_urls_set:
        return False

    sig = compute_minhash(item.content)
    if is_near_duplicate(sig, seen_sigs):
        seen_urls.add(url)  # suppress URL from further attempts too
        return False
    seen_urls.add(url)
    seen_sigs.append(sig)

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
        sent = (
            (per_ticker_sentiment or {}).get(symbol, item.sentiment)
            if per_ticker_sentiment is not None
            else item.sentiment
        )
        db.add(
            TickerMention(
                ticker=symbol,
                article_id=article.id,
                sentiment=sent,
                source_weight=0.9,
            )
        )
    return True


# ── Endpoint 1: General Market News ──────────────────────────────────────────

async def _fetch_general_news(
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict]:
    await _bucket.acquire()
    try:
        resp = await client.get(
            f"{_BASE}/news",
            params={"category": "general", "token": api_key},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
        logger.warning("finnhub_ingestor/general-news: HTTP %d", resp.status_code)
    except httpx.RequestError as exc:
        logger.warning("finnhub_ingestor/general-news: request error: %s", exc)
    return []


def _normalize_general(raw: dict, known: set[str]) -> NormalizedItem:
    headline = raw.get("headline") or ""
    summary = raw.get("summary") or headline
    url = raw.get("url") or ""
    text = f"{headline} {summary}"
    sentiment = analyze_sentiment(text)

    # Finnhub puts a 'related' field with comma-separated symbols on some items
    related_raw: str = raw.get("related") or ""
    explicit_tickers = [
        s.strip().upper()
        for s in related_raw.split(",")
        if s.strip().upper() in known
    ] if related_raw else []

    return NormalizedItem(
        id=f"finnhub-general-{url}",
        source="finnhub_general",
        title=headline or None,
        content=summary,
        timestamp=_ts_from_unix(raw.get("datetime")),
        url=url or None,
        tickers=explicit_tickers,  # NLP enrichment done in run()
        sentiment=sentiment,
    )


async def _run_general_news(
    db: AsyncSession,
    client: httpx.AsyncClient,
    api_key: str,
    known: set[str],
) -> list[NormalizedItem]:
    raw_items = await _fetch_general_news(client, api_key)
    if not raw_items:
        return []

    candidate_urls = [r.get("url", "") for r in raw_items if r.get("url")]
    existing = await _existing_urls(db, candidate_urls)
    seen: set[str] = set()
    seen_sigs: list[list[int]] = await load_recent_signatures(db)
    results: list[NormalizedItem] = []

    for raw in raw_items:
        item = _normalize_general(raw, known)
        if not item.url:
            continue

        # Enrich with DB-backed ticker extraction when no explicit tickers
        if not item.tickers:
            item.tickers = await extract_tickers_db(
                f"{item.title or ''} {item.content}", db
            )

        if await _persist(db, item, seen, existing, seen_sigs):
            results.append(item)

    await db.commit()
    logger.info("finnhub_ingestor/general-news: persisted %d articles", len(results))
    return results


# ── Endpoint 2: Company News ──────────────────────────────────────────────────

async def _fetch_company_news(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    await _bucket.acquire()
    try:
        resp = await client.get(
            f"{_BASE}/company-news",
            params={
                "symbol": symbol,
                "from": from_date,
                "to": to_date,
                "token": api_key,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
        logger.debug(
            "finnhub_ingestor/company-news: HTTP %d for %s", resp.status_code, symbol
        )
    except httpx.RequestError as exc:
        logger.debug(
            "finnhub_ingestor/company-news: request error for %s: %s", symbol, exc
        )
    return []


def _normalize_company(raw: dict, symbol: str) -> NormalizedItem:
    headline = raw.get("headline") or ""
    summary = raw.get("summary") or headline
    url = raw.get("url") or ""
    sentiment = analyze_sentiment(f"{headline} {summary}")

    return NormalizedItem(
        id=f"finnhub-company-{symbol}-{url}",
        source="finnhub_company",
        title=headline or None,
        content=summary,
        timestamp=_ts_from_unix(raw.get("datetime")),
        url=url or None,
        tickers=[symbol],  # ticker is explicit — no NLP needed
        sentiment=sentiment,
    )


async def _run_company_news(
    db: AsyncSession,
    client: httpx.AsyncClient,
    api_key: str,
) -> list[NormalizedItem]:
    symbols = await _load_symbols(db, limit=_MAX_TICKERS_PER_RUN)
    if not symbols:
        return []

    today = date.today()
    from_date = _date_str(today - timedelta(days=_COMPANY_NEWS_DAYS_BACK))
    to_date = _date_str(today)

    seen: set[str] = set()
    seen_sigs: list[list[int]] = await load_recent_signatures(db)
    results: list[NormalizedItem] = []

    for symbol in symbols:
        raw_items = await _fetch_company_news(
            client, api_key, symbol, from_date, to_date
        )

        if not raw_items:
            continue

        candidate_urls = [r.get("url", "") for r in raw_items if r.get("url")]
        existing = await _existing_urls(db, candidate_urls)

        for raw in raw_items:
            item = _normalize_company(raw, symbol)
            if not item.url:
                continue
            if await _persist(db, item, seen, existing, seen_sigs):
                results.append(item)

        await db.commit()

    logger.info(
        "finnhub_ingestor/company-news: persisted %d articles across %d tickers",
        len(results),
        len(symbols),
    )
    return results


# ── Endpoint 3: Insider Sentiment ─────────────────────────────────────────────

async def _fetch_insider_sentiment(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    await _bucket.acquire()
    try:
        resp = await client.get(
            f"{_BASE}/stock/insider-sentiment",
            params={
                "symbol": symbol,
                "from": from_date,
                "to": to_date,
                "token": api_key,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Response shape: {"data": [...], "symbol": "AAPL"}
            inner = data.get("data") if isinstance(data, dict) else None
            return inner if isinstance(inner, list) else []
        logger.debug(
            "finnhub_ingestor/insider-sentiment: HTTP %d for %s",
            resp.status_code,
            symbol,
        )
    except httpx.RequestError as exc:
        logger.debug(
            "finnhub_ingestor/insider-sentiment: request error for %s: %s",
            symbol,
            exc,
        )
    return []


def _mspr_to_sentiment(mspr: float) -> float:
    """
    Map MSPR (Monthly Share Purchase Ratio) → [-1.0, 1.0].

    MSPR = net_purchase / (|net_purchase| + |net_sale|).
    It already lives in [-1, 1], so we pass it through directly.
    Clamp to guard against malformed data.
    """
    return max(-1.0, min(1.0, float(mspr)))


def _normalize_insider(raw: dict, symbol: str) -> NormalizedItem | None:
    """
    Build a synthetic NormalizedItem from an insider-sentiment record.

    Each record represents one month of aggregated insider transactions.
    We craft a human-readable title so the article makes sense if displayed.
    """
    mspr = raw.get("mspr")
    change = raw.get("change")  # net shares bought (+) or sold (-)
    period = raw.get("year", "") and raw.get("month", "")

    if mspr is None:
        return None

    year = raw.get("year", "")
    month = raw.get("month", "")
    period_str = f"{year}-{month:02d}" if year and month else "unknown"

    sentiment = _mspr_to_sentiment(mspr)
    direction = "buying" if mspr >= 0 else "selling"
    net_shares = int(change) if change is not None else 0

    title = (
        f"{symbol} insiders net {direction} {abs(net_shares):,} shares "
        f"(MSPR {mspr:+.3f}) — {period_str}"
    )
    # Use a stable synthetic URL so deduplication works correctly across runs
    synthetic_url = f"finnhub://insider-sentiment/{symbol}/{period_str}"

    return NormalizedItem(
        id=f"finnhub-insider-{symbol}-{period_str}",
        source="finnhub_insider",
        title=title,
        content=title,  # no article body — title carries the signal
        timestamp=datetime.now(timezone.utc),
        url=synthetic_url,
        tickers=[symbol],
        sentiment=sentiment,
    )


async def _run_insider_sentiment(
    db: AsyncSession,
    client: httpx.AsyncClient,
    api_key: str,
) -> list[NormalizedItem]:
    symbols = await _load_symbols(db, limit=_MAX_TICKERS_PER_RUN)
    if not symbols:
        return []

    today = date.today()
    from_date = _date_str(today.replace(day=1) - timedelta(days=_INSIDER_MONTHS_BACK * 30))
    to_date = _date_str(today)

    seen: set[str] = set()
    seen_sigs: list[list[int]] = await load_recent_signatures(db)
    results: list[NormalizedItem] = []

    for symbol in symbols:
        records = await _fetch_insider_sentiment(
            client, api_key, symbol, from_date, to_date
        )

        if not records:
            continue

        candidate_urls = [
            f"finnhub://insider-sentiment/{symbol}/"
            f"{r.get('year', '')}-{r.get('month', ''):02d}"
            for r in records
            if r.get("year") and r.get("month")
        ]
        existing = await _existing_urls(db, candidate_urls)

        for raw in records:
            item = _normalize_insider(raw, symbol)
            if item is None:
                continue
            if await _persist(db, item, seen, existing, seen_sigs):
                results.append(item)

        await db.commit()

    logger.info(
        "finnhub_ingestor/insider-sentiment: persisted %d records across %d tickers",
        len(results),
        len(symbols),
    )
    return results


# ── Public entry points ───────────────────────────────────────────────────────

async def run_general(db: AsyncSession) -> list[NormalizedItem]:
    """Fetch general market news. Designed for the 30-min cron job."""
    api_key = getattr(settings, "finnhub_api_key", "")
    if not api_key:
        logger.info("finnhub_ingestor: FINNHUB_API_KEY not set — skipping")
        return []
    known: set[str] = {r[0] for r in (await db.execute(select(Ticker.symbol))).all()}
    async with httpx.AsyncClient() as client:
        return await _run_general_news(db, client, api_key, known)


async def run_company(db: AsyncSession) -> list[NormalizedItem]:
    """Fetch per-ticker company news. Designed for the 2-hour cron job."""
    api_key = getattr(settings, "finnhub_api_key", "")
    if not api_key:
        logger.info("finnhub_ingestor: FINNHUB_API_KEY not set — skipping")
        return []
    async with httpx.AsyncClient() as client:
        return await _run_company_news(db, client, api_key)


async def run_insider(db: AsyncSession) -> list[NormalizedItem]:
    """Fetch insider sentiment. Designed for the 6-hour cron job."""
    api_key = getattr(settings, "finnhub_api_key", "")
    if not api_key:
        logger.info("finnhub_ingestor: FINNHUB_API_KEY not set — skipping")
        return []
    async with httpx.AsyncClient() as client:
        return await _run_insider_sentiment(db, client, api_key)


async def run(db: AsyncSession) -> list[NormalizedItem]:
    """
    Convenience entry point that fires all three sub-ingestors sequentially.
    Used by the /ingest/finnhub-ingestor router endpoint and the /ingest/all handler.
    The shared token bucket ensures the combined call stream stays under 60 req/min.
    """
    api_key = getattr(settings, "finnhub_api_key", "")
    if not api_key:
        logger.info("finnhub_ingestor: FINNHUB_API_KEY not set — skipping")
        return []

    known: set[str] = {r[0] for r in (await db.execute(select(Ticker.symbol))).all()}
    results: list[NormalizedItem] = []

    async with httpx.AsyncClient() as client:
        results += await _run_general_news(db, client, api_key, known)
        results += await _run_company_news(db, client, api_key)
        results += await _run_insider_sentiment(db, client, api_key)

    logger.info("finnhub_ingestor: total persisted %d items", len(results))
    return results
