"""Pulls market news from Polygon.io ticker news endpoint and normalizes it."""
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

    recent_signatures = await load_recent_signatures(db)
    results: list[NormalizedItem] = []
    seen_sigs: list[list[int]] = list(recent_signatures)

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
            sig = compute_minhash(item.content)
            if is_near_duplicate(sig, seen_sigs):
                continue
            seen_sigs.append(sig)
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
                        source_weight=0.9,
                    )
                )
            results.append(item)

    await db.commit()
    return results
