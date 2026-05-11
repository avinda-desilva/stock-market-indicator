"""Pulls social posts from Twitter (Bearer Token) and optionally Reddit."""
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.config import settings
from app.models.article import Article
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.embeddings import encode
from app.utils.llm_analyzer import analyze_article
from app.utils.minhash_dedup import compute_minhash, is_near_duplicate, load_recent_signatures
from app.utils.ticker_extractor import extract_tickers_db

_TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
_TWITTER_QUERIES = [
    "stock market lang:en -is:retweet",
    "NYSE OR NASDAQ lang:en -is:retweet",
    "earnings report lang:en -is:retweet",
]
_TWEET_FIELDS = "id,text,created_at,author_id"
_MAX_RESULTS = 20


async def _ingest_twitter(db: AsyncSession) -> list[NormalizedItem]:
    if not settings.twitter_bearer_token:
        logger.warning("twitter_bearer_token not set — skipping Twitter ingest")
        return []

    recent_signatures = await load_recent_signatures(db)
    results: list[NormalizedItem] = []
    seen_ids: set[str] = set()
    seen_sigs: list[list[int]] = list(recent_signatures)
    headers = {"Authorization": f"Bearer {settings.twitter_bearer_token}"}

    async with httpx.AsyncClient(timeout=15) as client:
        for query in _TWITTER_QUERIES:
            resp = await client.get(
                _TWITTER_SEARCH_URL,
                headers=headers,
                params={
                    "query": query,
                    "max_results": _MAX_RESULTS,
                    "tweet.fields": _TWEET_FIELDS,
                },
            )
            logger.info("Twitter query=%r status=%d body=%s", query, resp.status_code, resp.text[:300])
            if resp.status_code != 200:
                continue
            for tweet in resp.json().get("data", []):
                if tweet["id"] in seen_ids:
                    continue
                seen_ids.add(tweet["id"])
                text = tweet.get("text", "")
                created = tweet.get("created_at") or datetime.now(timezone.utc).isoformat()
                if isinstance(created, str):
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))

                sig = compute_minhash(text)
                if is_near_duplicate(sig, seen_sigs):
                    continue
                seen_sigs.append(sig)

                candidate_tickers = await extract_tickers_db(text, db)
                if not candidate_tickers:
                    continue

                llm_tasks = [analyze_article(t, text) for t in candidate_tickers]
                analyses = await asyncio.gather(*llm_tasks)
                confirmed = [(a.ticker, a.sentiment, a.summary) for a in analyses if a is not None]
                if not confirmed:
                    continue

                item = NormalizedItem(
                    id=f"twitter-{tweet['id']}",
                    source="twitter",
                    title=None,
                    content=text,
                    timestamp=created,
                    url=f"https://twitter.com/i/web/status/{tweet['id']}",
                    tickers=[sym for sym, _, _ in confirmed],
                    sentiment=confirmed[0][1],
                )
                article = Article(
                    source=item.source,
                    title=item.title,
                    content=item.content,
                    url=item.url,
                    timestamp=item.timestamp,
                    minhash_signature=sig,
                    embedding=await encode(item.content),
                )
                db.add(article)
                await db.flush()
                for symbol, sentiment, summary in confirmed:
                    db.add(
                        TickerMention(
                            ticker=symbol,
                            article_id=article.id,
                            sentiment=sentiment,
                            source_weight=0.3,
                            llm_summary=summary,
                        )
                    )
                results.append(item)

    await db.commit()
    return results


async def _ingest_reddit(db: AsyncSession) -> list[NormalizedItem]:
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        return []

    import praw

    reddit = praw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
        read_only=True,
    )
    recent_signatures = await load_recent_signatures(db)
    seen_sigs: list[list[int]] = list(recent_signatures)
    results: list[NormalizedItem] = []
    for sub_name in ["stocks", "wallstreetbets", "investing", "StockMarket"]:
        for post in reddit.subreddit(sub_name).hot(limit=25):
            content = post.selftext or post.title
            ts = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
            text = f"{post.title} {content}"

            sig = compute_minhash(content)
            if is_near_duplicate(sig, seen_sigs):
                continue
            seen_sigs.append(sig)

            candidate_tickers = await extract_tickers_db(text, db)
            if not candidate_tickers:
                continue

            llm_tasks = [analyze_article(t, text) for t in candidate_tickers]
            analyses = await asyncio.gather(*llm_tasks)
            confirmed = [(a.ticker, a.sentiment, a.summary) for a in analyses if a is not None]
            if not confirmed:
                continue

            item = NormalizedItem(
                id=f"reddit-{post.id}",
                source="reddit",
                title=post.title,
                content=content,
                timestamp=ts,
                url=f"https://reddit.com{post.permalink}",
                tickers=[sym for sym, _, _ in confirmed],
                sentiment=confirmed[0][1],
            )
            article = Article(
                source=item.source,
                title=item.title,
                content=item.content,
                url=item.url,
                timestamp=item.timestamp,
                minhash_signature=sig,
                embedding=await encode(text),
            )
            db.add(article)
            await db.flush()
            for symbol, sentiment, summary in confirmed:
                db.add(
                    TickerMention(
                        ticker=symbol,
                        article_id=article.id,
                        sentiment=sentiment,
                        source_weight=0.2,
                        llm_summary=summary,
                    )
                )
            results.append(item)
    await db.commit()
    return results


async def run(db: AsyncSession) -> list[NormalizedItem]:
    twitter_results = await _ingest_twitter(db)
    reddit_results = await _ingest_reddit(db)
    return twitter_results + reddit_results
