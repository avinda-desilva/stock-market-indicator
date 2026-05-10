"""
GDELT 2.0 Global Knowledge Graph (GKG) ingestor — no API key required.

GDELT publishes a new 15-minute GKG CSV snapshot at:
  http://data.gdeltproject.org/gdeltv2/lastupdate.txt  (manifest)

The manifest has three lines:
  <bytes> <md5> <url>
The line whose URL ends in ".gkg.csv.zip" is the current GKG snapshot.

GKG V2.1 is a tab-delimited CSV with 27 columns (0-indexed):
  Col 0  GKGRECORDID
  Col 1  DATE              YYYYMMDDHHMMSS
  Col 4  DocumentIdentifier  (article URL)
  Col 7  Themes            semicolon-separated V1 theme codes
  Col 8  V2Themes          "THEME,char_offset;..." pairs
  Col 15 V2Tone            comma-delimited tone scores
  Col 22 Quotations
  Col 26 Extras            free-form; contains <PAGE_TITLE>...</PAGE_TITLE>

Finance/business theme filtering uses exact-prefix matching on V2Themes tokens
(each token is "THEME_CODE,offset" — we split on comma and check the theme part).
"""

import asyncio
import csv
import hashlib
import io
import logging
import re
import zipfile
from datetime import datetime, timezone

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

_MANIFEST_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
_FETCH_TIMEOUT = 60
_MAX_ITEMS_PER_RUN = 200

# GKG V2.1 column indices
_COL_DATE = 1
_COL_URL = 4
_COL_THEMES_V1 = 7
_COL_THEMES_V2 = 8
_COL_QUOTATIONS = 22
_COL_EXTRAS = 26
_MIN_COLS = 27

# Exact theme-code prefixes that indicate business/financial content.
# V2Themes tokens are formatted as "THEME_CODE,char_offset" — we split on the
# first comma and match the theme part against this set using startswith(), so
# "ECON_STOCKMARKET,349" and "ECON_STOCKMARKET_IPO,123" both match "ECON_STOCKMARKET".
_FINANCE_PREFIXES: tuple[str, ...] = (
    "ECON_STOCKMARKET",
    "ECON_TRADE",
    "ECON_CURRENCY",
    "ECON_BANKRUPTCY",
    "ECON_INFLATION",
    "ECON_INTEREST",
    "ECON_INVESTMENT",
    "ECON_RATECHANGE",
    "ECON_IPO",
    "ECON_TAXATION",
    "ECON_ENTREPRENEURSHIP",
    "ECON_REFORM",
    "BUS_MARKET",
    "BUS_EARNINGS",
    "BUS_ACQUISITION",
    "BUS_MERGER",
    "BUS_IPO",
    "BUS_LAYOFFS",
    "COMPANY_BANKRUPTCY",
    "CRISISLEX_CRISISLEXR4_BUSINESSAFFAIRS",
)

_PAGE_TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.DOTALL)


def _parse_gdelt_datetime(ts_str: str) -> datetime:
    try:
        return datetime.strptime(ts_str[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _themes_match(v2themes: str, v1themes: str) -> bool:
    """Return True if any token in V2Themes (or V1Themes fallback) is a finance theme."""
    # V2Themes: "ECON_STOCKMARKET,349;TAX_FNCACT_CEO,712;..."
    for token in v2themes.split(";"):
        theme_code = token.split(",")[0]
        if any(theme_code.startswith(p) for p in _FINANCE_PREFIXES):
            return True
    # V1Themes fallback (plain semicolon-separated codes, no offsets)
    if v1themes:
        for code in v1themes.split(";"):
            if any(code.startswith(p) for p in _FINANCE_PREFIXES):
                return True
    return False


def _extract_title(extras: str) -> str | None:
    m = _PAGE_TITLE_RE.search(extras)
    return m.group(1).strip() or None if m else None


def _parse_row(cols: list[str]) -> NormalizedItem | None:
    """Convert a GKG CSV row into a NormalizedItem. Returns None if unusable."""
    url = cols[_COL_URL].strip()
    if not url or not url.startswith("http"):
        return None

    title = _extract_title(cols[_COL_EXTRAS]) if len(cols) > _COL_EXTRAS else None

    quotations = cols[_COL_QUOTATIONS].strip() if len(cols) > _COL_QUOTATIONS else ""
    # Quotations field: "offset,count,speaker,quotation|..." — extract the text parts
    quote_texts: list[str] = []
    for entry in quotations.split("|"):
        parts = entry.split(",", 3)
        if len(parts) == 4 and parts[3]:
            quote_texts.append(parts[3].strip())
    content = " ".join(quote_texts[:3]) if quote_texts else (title or url)

    timestamp = _parse_gdelt_datetime(cols[_COL_DATE])
    text_for_sentiment = f"{title or ''} {content}"
    sentiment = analyze_sentiment(text_for_sentiment)

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    return NormalizedItem(
        id=f"gdelt-{url_hash}",
        source="gdelt",
        title=title,
        content=content,
        timestamp=timestamp,
        url=url,
        tickers=[],
        sentiment=sentiment,
    )


async def _get_gkg_csv_url(client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.get(_MANIFEST_URL, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("gdelt_ingestor: manifest fetch failed: %s", exc)
        return None

    for line in resp.text.splitlines():
        parts = line.strip().split()
        if len(parts) == 3 and parts[2].endswith(".gkg.csv.zip"):
            return parts[2]

    logger.warning("gdelt_ingestor: no .gkg.csv.zip entry in manifest")
    return None


async def _fetch_and_parse_csv(client: httpx.AsyncClient, url: str) -> list[list[str]]:
    """Download the GKG CSV zip and return rows as lists of column strings."""
    try:
        resp = await client.get(url, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("gdelt_ingestor: CSV fetch failed %s: %s", url, exc)
        return []

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = next((n for n in zf.namelist() if n.endswith(".csv")), None)
            if not csv_name:
                logger.warning("gdelt_ingestor: no CSV file inside zip")
                return []
            raw_text = zf.read(csv_name).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, Exception) as exc:
        logger.warning("gdelt_ingestor: zip parse error: %s", exc)
        return []

    reader = csv.reader(io.StringIO(raw_text), delimiter="\t", quoting=csv.QUOTE_NONE)
    return list(reader)


async def run(db: AsyncSession) -> list[NormalizedItem]:
    async with httpx.AsyncClient(
        headers={"User-Agent": "stock-market-indicator/1.0 gdelt-ingestor"},
        follow_redirects=True,
    ) as client:
        csv_url = await _get_gkg_csv_url(client)
        if not csv_url:
            return []

        logger.info("gdelt_ingestor: fetching %s", csv_url)
        rows = await _fetch_and_parse_csv(client, csv_url)

    if not rows:
        logger.info("gdelt_ingestor: empty CSV")
        return []

    # Filter rows to finance themes
    finance_rows = [
        r for r in rows
        if len(r) >= _MIN_COLS and _themes_match(r[_COL_THEMES_V2], r[_COL_THEMES_V1])
    ]
    logger.info(
        "gdelt_ingestor: %d total rows, %d match finance themes",
        len(rows), len(finance_rows),
    )

    # Normalize — over-fetch to have headroom after dedup
    candidates: list[NormalizedItem] = []
    for row in finance_rows[: _MAX_ITEMS_PER_RUN * 3]:
        item = _parse_row(row)
        if item:
            candidates.append(item)

    if not candidates:
        logger.info("gdelt_ingestor: no items after normalization")
        return []

    # Batch URL dedup against DB
    candidate_urls = [c.url for c in candidates if c.url]
    existing_rows = await db.execute(
        select(Article.url).where(Article.url.in_(candidate_urls))
    )
    existing_urls: set[str] = {r[0] for r in existing_rows}

    recent_signatures = await load_recent_signatures(db)
    seen_this_run: set[str] = set()
    seen_sigs: list[list[int]] = list(recent_signatures)
    results: list[NormalizedItem] = []

    for item in candidates:
        if len(results) >= _MAX_ITEMS_PER_RUN:
            break

        url = item.url or ""
        if not url or url in existing_urls or url in seen_this_run:
            continue
        seen_this_run.add(url)

        sig = compute_minhash(item.content)
        if is_near_duplicate(sig, seen_sigs):
            continue
        seen_sigs.append(sig)

        text_for_tickers = f"{item.title or ''} {item.content}"
        item.tickers = await extract_tickers_db(text_for_tickers, db)

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
                    source_weight=0.75,
                )
            )

        results.append(item)

    await db.commit()
    logger.info(
        "gdelt_ingestor: persisted %d new articles, %d total ticker mentions",
        len(results),
        sum(len(r.tickers) for r in results),
    )
    return results


# ---------------------------------------------------------------------------
# Standalone verification harness
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    async def _main():
        db_url = os.getenv("DATABASE_URL", "")

        if db_url:
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

            async_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            engine = create_async_engine(async_url, echo=False)
            SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with SessionLocal() as session:
                items = await run(session)
            await engine.dispose()
        else:
            import pathlib
            import tempfile

            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
            from sqlalchemy.pool import StaticPool

            tmp = pathlib.Path(tempfile.mkdtemp()) / "test.db"
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{tmp}",
                echo=False,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )

            from app.database import Base
            from app.models import article, ticker, ticker_mention  # noqa: F401

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

            from app.models.ticker import Ticker

            async with SessionLocal() as session:
                for sym, name, sector in [
                    ("AAPL", "Apple Inc.", "Technology"),
                    ("MSFT", "Microsoft Corporation", "Technology"),
                    ("TSLA", "Tesla Inc.", "Consumer Discretionary"),
                    ("AMZN", "Amazon.com Inc.", "Consumer Discretionary"),
                    ("JPM", "JPMorgan Chase & Co.", "Financials"),
                    ("GS", "Goldman Sachs Group Inc.", "Financials"),
                    ("XOM", "Exxon Mobil Corporation", "Energy"),
                    ("GE", "General Electric Company", "Industrials"),
                ]:
                    session.add(Ticker(symbol=sym, company_name=name, sector=sector))
                await session.commit()

            async with SessionLocal() as session:
                items = await run(session)

            await engine.dispose()

        print("\n" + "=" * 60)
        print(f"GDELT ingestor run complete — {len(items)} new articles inserted")
        print("=" * 60)
        if not items:
            print("(no new items — DB may already have this snapshot, or no finance rows found)")
        else:
            ticker_counter: dict[str, int] = {}
            for item in items:
                for sym in item.tickers:
                    ticker_counter[sym] = ticker_counter.get(sym, 0) + 1

            print("\nTop tickers extracted:")
            for sym, count in sorted(ticker_counter.items(), key=lambda x: -x[1])[:15]:
                print(f"  {sym:8s}  {count} mentions")

            print("\nSample articles (first 5):")
            for item in items[:5]:
                print(f"\n  [{item.source}] {item.timestamp.strftime('%Y-%m-%d %H:%M')} UTC")
                print(f"  Title    : {item.title or '(none)'}")
                print(f"  URL      : {item.url}")
                sent = f"{item.sentiment:+.3f}" if item.sentiment is not None else "n/a"
                print(f"  Sentiment: {sent}")
                print(f"  Tickers  : {', '.join(item.tickers) or '(none extracted)'}")
        print()

    asyncio.run(_main())
