"""Pulls market news from Polygon.io ticker news endpoint and normalizes it."""
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.article import Article
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.sentiment import analyze_sentiment
from app.utils.ticker_extractor import extract_tickers_db

_POLYGON_NEWS_URL = "https://api.polygon.io/v2/reference/news"


def _normalize(raw: dict) -> NormalizedItem:
    published = raw.get("published_utc") or datetime.now(timezone.utc).isoformat()
    if isinstance(published, str):
        published = datetime.fromisoformat(published.replace("Z", "+00:00"))
    title = raw.get("title") or ""
    content = raw.get("description") or title
    tickers = [t.upper() for t in raw.get("tickers", [])]
    return NormalizedItem(
        id=f"polygon-{raw['id']}",
        source="polygon",
        title=title,
        content=content,
        timestamp=published,
        url=raw.get("article_url"),
        tickers=tickers,
        sentiment=analyze_sentiment(f"{title} {content}"),
    )


async def run(db: AsyncSession) -> list[NormalizedItem]:
    if not settings.polygon_api_key:
        return []

    results: list[NormalizedItem] = []
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            _POLYGON_NEWS_URL,
            params={
                "limit": 50,
                "order": "desc",
                "sort": "published_utc",
                "apiKey": settings.polygon_api_key,
            },
        )
        if resp.status_code != 200:
            return []

        for raw in resp.json().get("results", []):
            item = _normalize(raw)
            # Polygon already returns tickers; still verify against DB dictionary
            item.tickers = await extract_tickers_db(
                f"{item.title or ''} {item.content}", db
            )
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
    return results
