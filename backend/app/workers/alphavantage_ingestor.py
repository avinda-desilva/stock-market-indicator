"""
Alpha Vantage News & Sentiment ingestor.

Free tier: 25 requests/day. Strategy: one request per sector topic so we
stay well within the daily cap across scheduled runs (7 topics = 7 req/run,
scheduler fires this every 6 hours = 28 req/day max — close but manageable;
reduce cron frequency if you hit the cap).

API docs: https://www.alphavantage.co/documentation/#news-sentiment
Endpoint: GET https://www.alphavantage.co/query?function=NEWS_SENTIMENT
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.article import Article
from app.models.ticker import Ticker
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.embeddings import encode
from app.utils.llm_analyzer import analyze_article
from app.utils.minhash_dedup import compute_minhash, is_near_duplicate, load_recent_signatures

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.alphavantage.co/query"

# Alpha Vantage topic slugs that map well to our sectors.
# Keeping this to 6 topics = 6 req/run, well within the 25/day free cap.
_TOPICS = [
    "technology",
    "finance",
    "energy_transportation",
    "manufacturing",
    "real_estate",
    "retail_wholesale",
]

_ARTICLES_PER_TOPIC = 50  # AV supports up to 1000; 50 is plenty per topic


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse AV's compact timestamp format: 20240501T120000 → datetime."""
    try:
        return datetime.strptime(ts_str, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _normalize(raw: dict) -> NormalizedItem:
    title = raw.get("title") or ""
    summary = raw.get("summary") or title
    url = raw.get("url") or ""

    # AV provides its own overall_sentiment_score; use it when present.
    av_score = raw.get("overall_sentiment_score")
    sentiment: float | None = None
    if av_score is not None:
        try:
            sentiment = float(av_score)
        except (TypeError, ValueError):
            pass

    # AV also returns per-ticker sentiment in ticker_sentiment list
    av_tickers = [
        t["ticker"].upper()
        for t in raw.get("ticker_sentiment", [])
        if isinstance(t.get("ticker"), str)
    ]

    return NormalizedItem(
        id=f"alphavantage-{url}",
        source="alphavantage",
        title=title,
        content=summary,
        timestamp=_parse_timestamp(raw.get("time_published", "")),
        url=url or None,
        tickers=av_tickers,
        sentiment=sentiment,
    )


async def _existing_urls(db: AsyncSession, urls: list[str]) -> set[str]:
    """Return the subset of urls already in the articles table."""
    if not urls:
        return set()
    rows = await db.execute(
        select(Article.url).where(Article.url.in_(urls))
    )
    return {r[0] for r in rows}


async def _known_symbols(db: AsyncSession) -> set[str]:
    rows = await db.execute(select(Ticker.symbol))
    return {r[0] for r in rows}


async def run(db: AsyncSession) -> list[NormalizedItem]:
    if not settings.alpha_vantage_api_key:
        logger.info("alphavantage_ingestor: ALPHA_VANTAGE_API_KEY not set — skipping")
        return []

    known = await _known_symbols(db)
    recent_signatures = await load_recent_signatures(db)
    results: list[NormalizedItem] = []
    seen_urls: set[str] = set()
    seen_sigs: list[list[int]] = list(recent_signatures)

    async with httpx.AsyncClient(timeout=20) as client:
        for topic in _TOPICS:
            try:
                resp = await client.get(
                    _BASE_URL,
                    params={
                        "function": "NEWS_SENTIMENT",
                        "topics": topic,
                        "limit": _ARTICLES_PER_TOPIC,
                        "sort": "LATEST",
                        "apikey": settings.alpha_vantage_api_key,
                    },
                )
            except httpx.RequestError as exc:
                logger.warning("alphavantage_ingestor: request error topic=%s: %s", topic, exc)
                continue

            if resp.status_code != 200:
                logger.warning("alphavantage_ingestor: HTTP %d for topic=%s", resp.status_code, topic)
                continue

            data = resp.json()

            # Rate-limit hit returns a Note field instead of feed
            if "Note" in data or "Information" in data:
                logger.warning(
                    "alphavantage_ingestor: rate limit hit — %s",
                    data.get("Note") or data.get("Information"),
                )
                break

            feed = data.get("feed", [])
            if not feed:
                logger.debug("alphavantage_ingestor: empty feed for topic=%s", topic)
                continue

            # Batch-check which URLs are already in DB
            batch_urls = [a.get("url", "") for a in feed]
            already_stored = await _existing_urls(db, batch_urls)

            for raw in feed:
                url = raw.get("url", "")
                if not url or url in seen_urls or url in already_stored:
                    continue
                seen_urls.add(url)

                item = _normalize(raw)

                sig = compute_minhash(item.content)
                if is_near_duplicate(sig, seen_sigs):
                    continue
                seen_sigs.append(sig)

                # AV-supplied tickers filtered to known symbols
                candidate_tickers = [s for s in item.tickers if s in known]
                if not candidate_tickers:
                    continue

                article_text = f"{item.title or ''}\n\n{item.content}"
                llm_tasks = [analyze_article(t, article_text) for t in candidate_tickers]
                analyses = await asyncio.gather(*llm_tasks)
                confirmed = [(a.ticker, a.sentiment, a.summary) for a in analyses if a is not None]
                if not confirmed:
                    continue

                item.tickers = [sym for sym, _, _ in confirmed]
                item.sentiment = confirmed[0][1]

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

                for symbol, sentiment, summary in confirmed:
                    # Prefer AV's per-ticker score when available; fall back to LLM
                    ticker_sent = sentiment
                    for ts in raw.get("ticker_sentiment", []):
                        if ts.get("ticker", "").upper() == symbol:
                            try:
                                ticker_sent = float(ts["ticker_sentiment_score"])
                            except (KeyError, TypeError, ValueError):
                                pass
                            break
                    db.add(
                        TickerMention(
                            ticker=symbol,
                            article_id=article.id,
                            sentiment=ticker_sent,
                            source_weight=1.0,
                            llm_summary=summary,
                        )
                    )

                results.append(item)

    await db.commit()
    logger.info("alphavantage_ingestor: persisted %d new articles", len(results))
    return results
