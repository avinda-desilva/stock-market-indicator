"""Pulls headlines from NewsAPI, normalizes, and persists to PostgreSQL."""
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.article import Article
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.embeddings import encode
from app.utils.minhash_dedup import compute_minhash, is_near_duplicate, load_recent_signatures
from app.utils.sentiment import analyze_sentiment
from app.utils.ticker_extractor import extract_tickers_db

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
        sentiment=analyze_sentiment(f"{title} {content}"),
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
                item.tickers = await extract_tickers_db(
                    f"{item.title or ''} {item.content}", db
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
    return results
