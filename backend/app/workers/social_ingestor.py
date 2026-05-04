"""Pulls social posts from Twitter (Bearer Token) and optionally Reddit."""
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.config import settings
from app.models.article import Article
from app.models.ticker_mention import TickerMention
from app.schemas.normalized import NormalizedItem
from app.utils.sentiment import analyze_sentiment
from app.utils.ticker_extractor import extract_tickers_db

_TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
_TWITTER_QUERIES = [
    "stock market lang:en -is:retweet",
    "NYSE OR NASDAQ lang:en -is:retweet",
    "earnings report lang:en -is:retweet",
]
_TWEET_FIELDS = "id,text,created_at,author_id"
_MAX_RESULTS = 20


def _normalize_tweet(tweet: dict) -> NormalizedItem:
    created = tweet.get("created_at") or datetime.now(timezone.utc).isoformat()
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    text = tweet.get("text", "")
    return NormalizedItem(
        id=f"twitter-{tweet['id']}",
        source="twitter",
        title=None,
        content=text,
        timestamp=created,
        url=f"https://twitter.com/i/web/status/{tweet['id']}",
        tickers=[],
        sentiment=analyze_sentiment(text),
    )


async def _ingest_twitter(db: AsyncSession) -> list[NormalizedItem]:
    if not settings.twitter_bearer_token:
        logger.warning("twitter_bearer_token not set — skipping Twitter ingest")
        return []

    results: list[NormalizedItem] = []
    seen_ids: set[str] = set()
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
                item = _normalize_tweet(tweet)
                item.tickers = await extract_tickers_db(item.content, db)
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
    results: list[NormalizedItem] = []
    for sub_name in ["stocks", "wallstreetbets", "investing", "StockMarket"]:
        for post in reddit.subreddit(sub_name).hot(limit=25):
            content = post.selftext or post.title
            ts = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
            item = NormalizedItem(
                id=f"reddit-{post.id}",
                source="reddit",
                title=post.title,
                content=content,
                timestamp=ts,
                url=f"https://reddit.com{post.permalink}",
                tickers=[],
                sentiment=analyze_sentiment(f"{post.title} {content}"),
            )
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


async def run(db: AsyncSession) -> list[NormalizedItem]:
    twitter_results = await _ingest_twitter(db)
    reddit_results = await _ingest_reddit(db)
    return twitter_results + reddit_results
