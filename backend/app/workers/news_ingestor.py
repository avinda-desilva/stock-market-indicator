"""Pulls headlines from NewsAPI, normalizes, and persists to PostgreSQL."""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.article import Article
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.embeddings import encode
from app.utils.llm_analyzer import analyze_article
from app.utils.minhash_dedup import compute_minhash, is_near_duplicate, load_recent_signatures
from app.utils.ticker_extractor import extract_tickers_db

logger = logging.getLogger(__name__)

_NEWSAPI_URL = "https://newsapi.org/v2/everything"
_FINANCE_QUERIES = ["stock market", "NYSE", "NASDAQ", "earnings", "IPO"]


def _normalize(raw: dict, source_id: str) -> NormalizedItem:
    published = raw.get("publishedAt") or datetime.now(timezone.utc).isoformat()
    if isinstance(published, str):
        published = datetime.fromisoformat(published.replace("Z", "+00:00"))
    content = raw.get("content") or raw.get("description") or ""
    title = raw.get("title") or ""
    return NormalizedItem(
        id=f"newsapi-{source_id}",
        source="newsapi",
        title=title,
        content=content,
        timestamp=published,
        url=raw.get("url"),
        tickers=[],
        sentiment=None,
    )


async def run(db: AsyncSession) -> list[NormalizedItem]:
    if not settings.news_api_key:
        return []

    recent_signatures = await load_recent_signatures(db)
    results: list[NormalizedItem] = []
    seen_sigs: list[list[int]] = list(recent_signatures)

    async with httpx.AsyncClient(timeout=15) as client:
        for query in _FINANCE_QUERIES:
            resp = await client.get(
                _NEWSAPI_URL,
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "apiKey": settings.news_api_key,
                },
            )
            if resp.status_code != 200:
                continue

            for raw in resp.json().get("articles", []):
                item = _normalize(raw, str(uuid.uuid4()))

                sig = compute_minhash(item.content)
                if is_near_duplicate(sig, seen_sigs):
                    continue
                seen_sigs.append(sig)

                # Candidate ticker extraction — regex/NER pass to get symbols to test
                candidate_tickers = await extract_tickers_db(
                    f"{item.title or ''} {item.content}", db
                )
                if not candidate_tickers:
                    continue

                article_text = f"{item.title or ''}\n\n{item.content}"

                # LLM analysis: one call per (article, ticker) pair, semaphore-throttled
                llm_tasks = [
                    analyze_article(ticker, article_text)
                    for ticker in candidate_tickers
                ]
                analyses = await asyncio.gather(*llm_tasks)

                confirmed: list[tuple[str, float, str]] = [
                    (a.ticker, a.sentiment, a.summary)
                    for a in analyses
                    if a is not None
                ]

                if not confirmed:
                    continue

                item.tickers = [sym for sym, _, _ in confirmed]

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
                    db.add(
                        TickerMention(
                            ticker=symbol,
                            article_id=article.id,
                            sentiment=sentiment,
                            source_weight=0.8,
                            llm_summary=summary,
                        )
                    )

                item.sentiment = confirmed[0][1] if confirmed else None  # representative sentiment for the item
                results.append(item)

    await db.commit()
    logger.info("news_ingestor: persisted %d articles", len(results))
    return results
