# Stock Market Indicator — System Design Document

## Table of Contents
1. [Problem Statement](#1-problem-statement)
2. [Solution Overview](#2-solution-overview)
3. [System Architecture](#3-system-architecture)
4. [System 1 — Data Pipeline](#4-system-1--data-pipeline)
5. [System 2 — Backend API](#5-system-2--backend-api)
6. [System 3 — Frontend UI](#6-system-3--frontend-ui)
7. [Full Tech Stack](#7-full-tech-stack)
8. [Data Sources & API Keys](#8-data-sources--api-keys)
9. [Data Flow: Ingestion to Trending Score](#9-data-flow-ingestion-to-trending-score)
10. [Database Schema](#10-database-schema)
11. [Ranking Algorithm](#11-ranking-algorithm)
12. [Search Query Pipeline](#12-search-query-pipeline)
13. [Caching Strategy](#13-caching-strategy)
14. [Infrastructure & Deployment](#14-infrastructure--deployment)

---

## 1. Problem Statement

Financial markets generate enormous volumes of news, social media activity, and market data every minute. Retail investors and analysts lack a single, real-time surface that:

- Aggregates financial content from multiple heterogeneous sources (news, social media, market APIs, RSS feeds)
- Identifies *which* tickers are gaining abnormal attention right now versus their rolling baseline
- Quantifies sentiment direction (bullish vs bearish) at the ticker and sector level
- Provides a searchable interface that understands natural language queries like "AI stocks" or "TSLA earnings"

Existing tools either require expensive subscriptions, expose raw data without synthesis, or fail to surface sector-level trending signals.

---

## 2. Solution Overview

**Stock Market Indicator (SMI)** is a sector-based trending ticker and financial search engine. It continuously ingests articles and social posts from five distinct data sources, extracts ticker mentions via NLP, scores each ticker using a weighted recency + sentiment + spike formula, and presents a live-updating dashboard with sector filtering, time-window controls, and a full-text search engine.

The application is organized as three cooperating systems:

| # | System | Responsibility |
|---|--------|---------------|
| 1 | Data Pipeline | Ingest, normalize, deduplicate, and store articles; seed the ticker dictionary |
| 2 | Backend API | Persist data, run the ranking engine, serve REST endpoints, maintain Redis cache |
| 3 | Frontend UI | Real-time dashboard, sector navigation, ticker detail pages, search palette |

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         External Data Sources                        │
│  NewsAPI  │  Alpha Vantage  │  Polygon.io  │  Twitter/Reddit  │  Yahoo RSS │
└─────┬──────────────┬──────────────┬────────────────┬────────────────┘
      │              │              │                │
      ▼              ▼              ▼                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                System 1 — Data Pipeline (APScheduler)                │
│                                                                      │
│  news_ingestor  │  alphavantage_ingestor  │  market_ingestor         │
│  social_ingestor  │  yahoo_rss_ingestor   │  ranking_engine          │
│                                                                      │
│  Normalize → Sentiment (TextBlob) → Ticker Extraction → Deduplicate  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ SQLAlchemy async writes
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                   PostgreSQL  (smi_postgres)                       │
│   tickers  │  articles  │  ticker_mentions                         │
└───────────────────────┬───────────────────────────────────────────┘
                        │                    ▲
                        │ ranking_engine      │ live DB queries
                        ▼                    │
┌───────────────────────────────────────────────────────────────────┐
│                    Redis  (smi_redis)                              │
│  trending:global  │  trending:{Sector}  — TTL 90 s                 │
└───────────────────────┬───────────────────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────────────────┐
│               System 2 — FastAPI Backend  (smi_backend)            │
│                                                                    │
│  /trending  │  /ticker/{symbol}  │  /search  │  /ingest/*          │
└───────────────────────┬───────────────────────────────────────────┘
                        │  Axios HTTP
                        ▼
┌───────────────────────────────────────────────────────────────────┐
│              System 3 — Next.js Frontend  (smi_frontend)           │
│                                                                    │
│  Dashboard  │  Sector Nav  │  Time Filter  │  Ticker Detail         │
│  Sentiment Chart  │  News Timeline  │  Search Palette               │
└───────────────────────────────────────────────────────────────────┘
```

---

## 4. System 1 — Data Pipeline

The data pipeline is the ingestion and scoring layer. It runs entirely within the **backend container** as scheduled async jobs managed by APScheduler. A one-shot seed container (`smi_pipeline`) also runs on startup to pre-populate the database before the UI loads.

### 4.1 Ingestion Workers

Each worker follows the same contract: accept an `AsyncSession`, fetch data from its source, normalize into a `NormalizedItem`, run sentiment analysis and ticker extraction, deduplicate by URL, persist to PostgreSQL, and commit.

#### news_ingestor — NewsAPI
- **Source:** `https://newsapi.org/v2/everything`
- **Schedule:** Every 30 minutes
- **Volume:** 5 finance queries × 20 articles per query = up to 100 articles/run
- **Queries:** `["stock market", "NYSE", "NASDAQ", "earnings", "IPO"]`
- **Key required:** `NEWS_API_KEY`
- **Process:** Fetches article headline + content → TextBlob sentiment → DB ticker extraction → upsert

#### alphavantage_ingestor — Alpha Vantage
- **Source:** `https://www.alphavantage.co/query?function=NEWS_SENTIMENT`
- **Schedule:** Every 6 hours
- **Volume:** 6 sector topics × 50 articles = up to 300 articles/run (24 requests/day, within 25/day free cap)
- **Topics:** `technology`, `finance`, `energy_transportation`, `manufacturing`, `real_estate`, `retail_wholesale`
- **Key required:** `ALPHA_VANTAGE_API_KEY`
- **Special behavior:** Alpha Vantage provides its own `overall_sentiment_score` and per-ticker `ticker_sentiment_score`. These float scores are preferred over TextBlob when available. The worker merges AV-supplied ticker symbols with our DB-verified ticker extractor.

#### market_ingestor — Polygon.io
- **Source:** `https://api.polygon.io/v2/reference/news`
- **Schedule:** Hourly at :00
- **Volume:** 50 articles/run
- **Key required:** `POLYGON_API_KEY`
- **Process:** Polygon returns article-level ticker arrays; these are cross-validated against the DB ticker dictionary before being saved as `TickerMention` rows.

#### social_ingestor — Twitter/X & Reddit
- **Source:**
  - Twitter: `https://api.twitter.com/2/tweets/search/recent`
  - Reddit: PRAW library, subreddits `r/stocks`, `r/wallstreetbets`, `r/investing`, `r/StockMarket`
- **Schedule:** Every 15 minutes
- **Volume:** 3 queries × 20 tweets + 4 subreddits × 25 posts
- **Keys required:** `TWITTER_BEARER_TOKEN` (paid tier), `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`
- **Behavior:** Gracefully skips if credentials are absent. Twitter queries filter language to English and exclude retweets.

#### yahoo_rss_ingestor — Yahoo Finance RSS
- **Source:** `https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}`
- **Schedule:** Hourly at :30
- **Volume:** ~50 tickers × 15 items/feed = ~750 article candidates/run, minus duplicates
- **Key required:** None (public RSS feed)
- **Process:** Fetches all tracked ticker symbols concurrently (capped at 5 simultaneous requests via asyncio Semaphore). Parses XML with stdlib `xml.etree`. Each feed item is inherently tied to a known ticker, so no NLP extraction is needed for ticker association.

### 4.2 Ticker Extraction (`utils/ticker_extractor.py`)

When article text needs to be scanned for ticker mentions:
1. A regex `\$?([A-Z]{1,5})\b` finds all uppercase word tokens (with optional `$` prefix)
2. A stopword filter removes common false positives (`A`, `I`, `AN`, `FED`, `CEO`, `ETF`, etc.)
3. Remaining candidates are cross-referenced against the `tickers` table in PostgreSQL — only known tickers are kept

### 4.3 Sentiment Analysis (`utils/sentiment.py`)

All text-level sentiment uses **TextBlob** polarity:
- Returns a `float` in `[-1.0, 1.0]`
- Applied to `"{title} {content}"` concatenation
- Alpha Vantage articles may override this with their own pre-computed score

### 4.4 Deduplication

All workers deduplicate by `Article.url` before inserting. The pattern is:
1. Collect candidate URLs in the current batch
2. Query `SELECT url FROM articles WHERE url IN (...)` in one round trip
3. Skip any URL already present; also skip within-batch duplicates via `seen_urls` set

### 4.5 Ranking Engine

- **Schedule:** Every minute
- **Storage:** Redis keys `trending:global` and `trending:{Sector}`, TTL = 90 seconds

The ranking engine scores every ticker in the database each minute and writes the results to Redis. See [Section 11](#11-ranking-algorithm) for the formula.

### 4.6 Nightly Cleanup

A cleanup job runs at 23:59 UTC daily. It deletes `Article` rows (and cascades to `TickerMention`) older than 7 days, keeping the database from growing unbounded.

### 4.7 One-Shot Seed Pipeline (`smi_pipeline` container)

On startup, a separate Docker container (`smi_pipeline`) runs `data-pipeline/run_ingestors.py all`. This triggers all five ingestors once in sequence to pre-populate the database before any user loads the dashboard. The container exits after completion (`restart: "no"`).

Ticker seed data lives in `data-pipeline/seeds/tickers_seed.sql` — a SQL INSERT file seeding the `tickers` table with symbols, company names, and sectors.

---

## 5. System 2 — Backend API

The backend is a **FastAPI** application running on port 8000 inside the `smi_backend` container. It serves as both the REST API for the frontend and the host for all scheduled ingestion workers (via APScheduler in the same process).

### 5.1 Application Startup

On startup (`lifespan` context manager in `main.py`):
1. SQLAlchemy runs `Base.metadata.create_all` (development convenience; Alembic handles production migrations)
2. APScheduler is started — all ingestion cron jobs and the ranking engine are registered
3. On shutdown, the scheduler is stopped and the async database engine is disposed

### 5.2 REST Endpoints

#### Ingestion (POST) — manual trigger
| Endpoint | Worker |
|----------|--------|
| `POST /ingest/news-ingestor` | `news_ingestor` |
| `POST /ingest/social-ingestor` | `social_ingestor` |
| `POST /ingest/market-ingestor` | `market_ingestor` |
| `POST /ingest/alphavantage-ingestor` | `alphavantage_ingestor` |
| `POST /ingest/yahoo-rss-ingestor` | `yahoo_rss_ingestor` |

#### Tickers (GET)
| Endpoint | Description |
|----------|-------------|
| `GET /tickers/` | List all tracked tickers |
| `POST /tickers/` | Add a new ticker |
| `GET /ticker/{symbol}` | Ticker profile + trend metrics; `?window=6h\|24h\|3d\|7d` |
| `GET /ticker/{symbol}/news` | Recent articles for a ticker; `?limit=&window=` |

#### Trending (GET)
All trending endpoints accept `?window=6h|24h|3d|7d` (default `24h`). The Redis cache is **only** used for the `24h` global view; all other windows query PostgreSQL live.

| Endpoint | Description |
|----------|-------------|
| `GET /trending` | Global top-10 (Redis for 24h, live DB otherwise) |
| `GET /trending?sector=AI` | Sector top-10 (keyword or canonical sector name) |
| `GET /trending?window=7d` | Live DB query for non-24h windows |
| `GET /trending/sector/{sector}` | Sector top-10 via path parameter |
| `GET /trending/sectors` | List all sector keys currently in Redis cache |

#### Search (GET)
| Endpoint | Description |
|----------|-------------|
| `GET /search?q=` | Full query pipeline — returns `{query, intent, tickers, news, trend_data}` |

#### Health
| Endpoint | Description |
|----------|-------------|
| `GET /health` | Returns `{"status": "ok"}` |

### 5.3 Data Models (SQLAlchemy)

| Model | Table | Key Columns |
|-------|-------|-------------|
| `Ticker` | `tickers` | `symbol` (PK), `company_name`, `sector` |
| `Article` | `articles` | `id`, `source`, `title`, `content`, `url`, `timestamp` |
| `TickerMention` | `ticker_mentions` | `id`, `ticker` (FK → tickers), `article_id` (FK → articles), `sentiment` |

Time-window queries always filter on `Article.timestamp` (publication time), not `TickerMention.created_at` (ingestion time). This ensures that a 6-hour window reflects when news was published, not when SMI happened to process it.

### 5.4 Database — PostgreSQL

- Image: `postgres:15-alpine`
- Async driver: `asyncpg`
- ORM: SQLAlchemy 2.0 (async session pattern)
- Migrations: Alembic (`app/migrations/versions/0001_initial_schema.py`)
- Persistence: Docker named volume `postgres_data`

### 5.5 Cache — Redis

- Image: `redis:7-alpine`
- Client: `redis.asyncio` (async)
- Keys: `trending:global`, `trending:{Sector}` — e.g., `trending:Technology`
- TTL: 90 seconds per key
- Password-protected (`REDIS_PASSWORD` env var)
- Persistence: Docker named volume `redis_data`

### 5.6 Scheduler (APScheduler)

| Job ID | Trigger | Function |
|--------|---------|----------|
| `news_ingestor` | `*/30 * * * *` | `_run_news()` |
| `social_ingestor` | `*/15 * * * *` | `_run_social()` |
| `market_ingestor` | `0 * * * *` | `_run_market()` |
| `alphavantage_ingestor` | `0 */6 * * *` | `_run_alphavantage()` |
| `yahoo_rss_ingestor` | `30 * * * *` | `_run_yahoo_rss()` |
| `ranking_engine` | `* * * * *` | `_run_ranking()` |
| `cleanup` | `59 23 * * *` | `_run_cleanup()` |

---

## 6. System 3 — Frontend UI

The frontend is a **Next.js 16** application (App Router, React 19, TypeScript) running on port 3000 inside the `smi_frontend` container.

### 6.1 Routes

| Route | File | Description |
|-------|------|-------------|
| `/` | `app/page.tsx` | Trending dashboard |
| `/ticker/[symbol]` | `app/ticker/[symbol]/page.tsx` | Ticker detail page |

Both routes sync `?window=` and `?sector=` as URL search params via `router.replace` so the state is bookmarkable and shareable.

### 6.2 Dashboard (`/`)

- **HeroTickers** — top 3 trending tickers displayed prominently with score and spike badge
- **SectorNav** — horizontal pill navigation across all sectors returned by `/trending/sectors`; "All" deselects sector filter
- **TimeFilter** — 4 buttons: `6h`, `24h`, `3d`, `7d`; changes the `window` query param and re-fetches trending data
- **TickerCard** — grid card showing: symbol, company name, sector, mention counts (1h / 24h), sentiment badge, score bar relative to the max score in the current result set, spike indicator
- **SkeletonCard** — placeholder shimmer shown during loading
- **Refresh button** — silent re-fetch (spinner only; no full loading state)

### 6.3 Ticker Detail (`/ticker/[symbol]`)

- **SentimentChart** — Recharts line chart of sentiment over time for the selected window
- **NewsTimeline** — Chronological list of articles mentioning the ticker, with source badge, sentiment badge, publication timestamp, and external link

### 6.4 Search (`SearchPalette`)

- Global search palette (keyboard-accessible)
- Calls `GET /search?q=` on each query
- Displays results grouped by intent: tickers, news articles, trend data
- Intent is detected server-side by the query pipeline; results are surfaced accordingly

### 6.5 API Client (`lib/api.ts`)

All requests go through an Axios instance configured with:
- `baseURL`: `NEXT_PUBLIC_API_URL` env var (defaults to `http://localhost:8000` locally; inside Docker it proxies through `/api` to `http://backend:8000` via Next.js rewrites)
- `timeout`: 10,000 ms

Typed fetch functions:
- `getTrending(sector?, window)` → `TrendingTicker[]`
- `getTrendingSectors()` → `string[]`
- `getTickerDetail(symbol, window)` → `TickerDetail`
- `getTickerNews(symbol, limit, window)` → `Article[]`
- `searchTickers(query)` → `SearchResult`

---

## 7. Full Tech Stack

### Backend
| Package | Version | Role |
|---------|---------|------|
| Python | 3.11+ | Runtime |
| FastAPI | 0.115.12 | Web framework + async routing |
| Uvicorn | 0.34.2 | ASGI server |
| SQLAlchemy | 2.0.41 | Async ORM |
| asyncpg | 0.30.0 | PostgreSQL async driver |
| Alembic | 1.15.2 | Database migrations |
| Redis (redis.asyncio) | 5.2.1 | Async Redis client |
| Pydantic | 2.11.4 | Data validation + settings |
| pydantic-settings | 2.9.1 | `.env` config management |
| httpx | 0.28.1 | Async HTTP client for external APIs |
| TextBlob | 0.19.0 | NLP sentiment analysis |
| APScheduler | 3.11.0 | In-process async cron scheduler |
| newsapi-python | 0.2.7 | NewsAPI SDK |
| PRAW | 7.8.1 | Reddit API client |
| Tweepy | 4.15.0 | Twitter API client |
| python-dotenv | 1.1.0 | `.env` loading |

### Frontend
| Package | Version | Role |
|---------|---------|------|
| Next.js | 16.2.4 | Framework (App Router) |
| React | 19.2.4 | UI runtime |
| TypeScript | 5.x | Type safety |
| Tailwind CSS | 4.x | Utility-first styling |
| Framer Motion | 12.x | Page and card animations |
| Recharts | 3.x | Sentiment timeline chart |
| Lucide React | 1.x | Icon library |
| Axios | 1.x | HTTP client |
| react-hot-toast | 2.x | Toast notifications |

### Infrastructure
| Component | Technology | Version |
|-----------|-----------|---------|
| Database | PostgreSQL | 15-alpine |
| Cache | Redis | 7-alpine |
| Containerization | Docker + Docker Compose | v3.9 |
| Container orchestration | Docker Compose | — |

---

## 8. Data Sources & API Keys

### NewsAPI
- **Variable:** `NEWS_API_KEY`
- **Endpoint:** `https://newsapi.org/v2/everything`
- **What it provides:** English-language financial news articles (headline, description, full content, publication timestamp, source URL)
- **How it's used:** Queried with 5 finance-focused keyword queries. Articles are normalized, sentiment-scored, and ticker-extracted before being stored.
- **Rate limit:** Free tier: 100 requests/day, 1,000 articles/day

### Alpha Vantage
- **Variable:** `ALPHA_VANTAGE_API_KEY`
- **Endpoint:** `https://www.alphavantage.co/query?function=NEWS_SENTIMENT`
- **What it provides:** Financial news feed with pre-computed overall sentiment scores and per-ticker sentiment scores
- **How it's used:** Queried across 6 sector topic slugs every 6 hours. AV's own sentiment scores are used directly when available (bypassing TextBlob). AV also returns `ticker_sentiment[]` arrays, which are merged with our DB-verified ticker extraction.
- **Rate limit:** Free tier: 25 requests/day. The 6-topic schedule uses 24 requests/day (6 topics × 4 runs).

### Polygon.io
- **Variable:** `POLYGON_API_KEY`
- **Endpoint:** `https://api.polygon.io/v2/reference/news`
- **What it provides:** Market news articles with associated ticker arrays, publication timestamps
- **How it's used:** Fetches 50 most recent articles per hourly run. Polygon-provided ticker arrays are cross-validated against the `tickers` table before creating `TickerMention` rows.
- **Rate limit:** Free tier: unlimited reads at limited speed

### Twitter / X
- **Variable:** `TWITTER_BEARER_TOKEN`
- **Endpoint:** `https://api.twitter.com/2/tweets/search/recent`
- **What it provides:** Recent English-language tweets matching financial search queries
- **How it's used:** 3 query templates (stock market, exchange names, earnings) fetch up to 20 tweets each. Tweet text is run through TextBlob sentiment and DB ticker extraction.
- **Rate limit:** Requires paid API access (Basic tier or higher)
- **Behavior:** Gracefully skips if token is not set

### Reddit
- **Variables:** `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`
- **Library:** PRAW (Python Reddit API Wrapper)
- **What it provides:** Top posts from financial subreddits (r/stocks, r/wallstreetbets, r/investing, r/StockMarket)
- **How it's used:** Fetches 25 hot posts per subreddit. Post title + body is run through TextBlob sentiment and DB ticker extraction.
- **Behavior:** Gracefully skips if credentials are not set

### Yahoo Finance RSS
- **No API key required**
- **Endpoint:** `https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}`
- **What it provides:** Public per-ticker RSS news feeds (headline, link, description, publication date)
- **How it's used:** All ticker symbols in the DB are iterated. Feeds are fetched concurrently (max 5 simultaneous). Since the feed is per-ticker, no NLP ticker extraction is needed — the ticker is known from the feed URL. Each item's headline + description is TextBlob-scored.
- **Volume:** ~50 tickers × 15 items = ~750 candidates/run (after deduplication, much less)

---

## 9. Data Flow: Ingestion to Trending Score

This section traces the full lifecycle of a single article from external API to the trending dashboard.

```
1. FETCH
   APScheduler fires news_ingestor every 30 min
   → httpx GET https://newsapi.org/v2/everything?q=earnings&...
   → receives JSON array of raw articles

2. NORMALIZE
   _normalize(raw) → NormalizedItem {
     source: "newsapi",
     title: "Apple beats Q3 earnings expectations",
     content: "Apple Inc. reported...",
     timestamp: 2026-05-04T14:32:00Z,
     url: "https://...",
     tickers: [],
     sentiment: 0.0  ← placeholder
   }

3. SENTIMENT
   analyze_sentiment("{title} {content}")
   → TextBlob("{title} {content}").sentiment.polarity
   → 0.42  ← stored in NormalizedItem.sentiment

4. TICKER EXTRACTION
   extract_tickers_db("Apple beats Q3 earnings expectations Apple Inc. reported...", db)
   → regex finds ["APPLE", "Q", "INC"]
   → stopword filter removes ["Q", "INC"]
   → DB lookup: SELECT symbol FROM tickers WHERE symbol IN ('APPLE')
   → cross-reference returns []  (no match for "APPLE")
   → separately regex finds "AAPL" if present in text
   → returns ["AAPL"]

5. DEDUPLICATE
   SELECT url FROM articles WHERE url = 'https://...'
   → empty result → not a duplicate, proceed

6. PERSIST
   INSERT INTO articles (source, title, content, url, timestamp) VALUES (...)
   → article.id = 9821

   INSERT INTO ticker_mentions (ticker, article_id, sentiment)
   VALUES ('AAPL', 9821, 0.42)

7. COMMIT
   db.commit()

8. RANKING ENGINE (runs every minute)
   SELECT ticker, COUNT(*) FROM ticker_mentions
     JOIN articles ON articles.id = ticker_mentions.article_id
     WHERE articles.timestamp >= NOW() - INTERVAL '1 hour'
     GROUP BY ticker
   → mentions_1h: {AAPL: 14, TSLA: 8, NVDA: 21, ...}

   SELECT ticker, COUNT(*) FROM ticker_mentions ... WHERE >= NOW() - 24h
   → mentions_24h: {AAPL: 87, TSLA: 55, NVDA: 143, ...}

   SELECT ticker, AVG(sentiment) FROM ticker_mentions ... WHERE >= NOW() - 24h
   → sentiment_24h: {AAPL: 0.31, TSLA: -0.12, NVDA: 0.58, ...}

   score(NVDA) = (21 × 3) + (143 × 1.5) + (0.58 × 2) + (0.0 × 2)
              = 63 + 214.5 + 1.16 + 0
              = 278.66

   spike_check: hourly_avg = 143/24 = 5.96
                21 > 5.96 × 2 = 11.92  → TRUE → spike boost
   score(NVDA) = 278.66 × 1.5 = 417.99

9. REDIS WRITE
   SET trending:global '[{"symbol":"NVDA","score":417.99,...}, ...]'  EX 90
   SET trending:Technology '[{"symbol":"NVDA","score":417.99,...}, ...]'  EX 90

10. API SERVE
    GET /trending → reads trending:global from Redis → returns JSON array

11. FRONTEND RENDER
    Axios GET /trending → React state update → TickerCard grid re-renders
    NVDA shows score bar at 100% of maxScore, spike badge visible
```

---

## 10. Database Schema

### `tickers`
| Column | Type | Notes |
|--------|------|-------|
| `symbol` | VARCHAR (PK) | Uppercase ticker, e.g. `AAPL` |
| `company_name` | VARCHAR | Human-readable name |
| `sector` | VARCHAR | GICS sector, e.g. `Technology` |

### `articles`
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER (PK, auto) | |
| `source` | VARCHAR | `newsapi`, `alphavantage`, `polygon`, `twitter`, `reddit`, `yahoo_rss` |
| `title` | TEXT | Nullable |
| `content` | TEXT | Body or description |
| `url` | TEXT (UNIQUE) | Deduplication key |
| `timestamp` | TIMESTAMPTZ | Publication time (used for all window filters) |

### `ticker_mentions`
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER (PK, auto) | |
| `ticker` | VARCHAR (FK → tickers.symbol) | |
| `article_id` | INTEGER (FK → articles.id, CASCADE DELETE) | |
| `sentiment` | FLOAT | Polarity score in `[-1.0, 1.0]` |

> **Design note:** `Article.timestamp` is the article's publication time. `TickerMention.created_at` (ingestion time) is intentionally excluded from all window-filter queries. This means the `24h` window shows articles *published* in the last 24 hours, not articles *ingested* in that period.

---

## 11. Ranking Algorithm

The ranking engine runs every minute. For each ticker in the database:

```
score = (mentions_1h × 3) + (mentions_24h × 1.5) + (sentiment_24h × 2) + (price_change_pct × 2)
```

**Spike boost:** If the last hour's mention count exceeds twice the rolling hourly average:
```
if mentions_1h > (mentions_24h / 24) × 2:
    score = score × 1.5
```

**Weight rationale:**
- `mentions_1h × 3` — highest weight; recent velocity is the strongest trending signal
- `mentions_24h × 1.5` — provides volume context and prevents one-article spikes from dominating
- `sentiment × 2` — positive sentiment lifts score; negative suppresses it
- `price_change_pct × 2` — reserved for future integration with market data (currently defaults to 0.0)

**Output:**
- `trending:global` — top-10 across all sectors, sorted by score descending
- `trending:{Sector}` — top-10 per sector (e.g. `trending:Technology`, `trending:Energy`)
- Each key expires in **90 seconds**, so stale scores are never served for more than 90s after the ranking engine updates them

---

## 12. Search Query Pipeline

The search system (`utils/query_pipeline.py` + `routers/search.py`) processes free-text queries into typed, intent-driven responses.

### Intent Classification

```
Input: raw query string
↓
1. Sector keyword scan   →  "AI stocks" → sectors: ["Technology"]
2. Ticker regex scan     →  "$TSLA"     → tickers: ["TSLA"]
3. News keyword check    →  "earnings"  → news_search
4. Catch-all fallback    →              → general_search
```

| Intent | Example Query | Backend Action |
|--------|---------------|----------------|
| `sector_search` | "AI stocks", "biotech" | Filter trending results by matched sector |
| `ticker_lookup` | "AAPL", "$TSLA" | Return ticker profile + trend data directly |
| `news_search` | "earnings report", "beat estimates" | Full-text search on article titles/content |
| `general_search` | "cloud computing growth" | `ILIKE` FTS across tickers and articles |

### Response Schema

```json
{
  "query": "AI stocks",
  "intent": "sector_search",
  "tickers": [
    { "symbol": "NVDA", "score": 417.99, "mentions_24h": 143, "sentiment": 0.58 }
  ],
  "news": [
    { "id": 9821, "source": "alphavantage", "title": "...", "url": "...", "timestamp": "...", "sentiment": 0.42 }
  ],
  "trend_data": [
    { "symbol": "NVDA", "mentions_1h": 21, "mentions_24h": 143, "sentiment": 0.58, "score": 417.99, "spike": true }
  ]
}
```

### Sector Keyword Map (partial)

| Query keyword | Canonical sector |
|---------------|-----------------|
| `ai`, `artificial intelligence`, `tech`, `semiconductor` | Technology |
| `energy`, `oil`, `solar`, `renewable` | Energy |
| `health`, `pharma`, `biotech`, `medical` | Health Care |
| `finance`, `bank`, `insurance` | Financials |
| `consumer`, `retail`, `ecommerce` | Consumer Discretionary |
| `defense`, `aerospace`, `industrial` | Industrials |
| `reit`, `real estate` | Real Estate |
| `telecom`, `media`, `communication` | Communication Services |

---

## 13. Caching Strategy

| Cache Key | Source | TTL | Fallback |
|-----------|--------|-----|---------|
| `trending:global` | ranking_engine (every 1 min) | 90 s | Live PostgreSQL query |
| `trending:{Sector}` | ranking_engine (every 1 min) | 90 s | Live PostgreSQL query |

Only the `24h` global and sector views are cached in Redis. All other time windows (`6h`, `3d`, `7d`) bypass Redis and query PostgreSQL directly, since those views are less frequently requested and require time-range filtering that varies per request.

The 90-second TTL ensures that even if the ranking engine misses a run or Redis is temporarily unavailable, data served is at most 90 seconds stale. The FastAPI trending router falls back to a live DB query if the Redis key is absent.

---

## 14. Infrastructure & Deployment

### Docker Compose Services

| Container | Image | Port | Role |
|-----------|-------|------|------|
| `smi_postgres` | `postgres:15-alpine` | 5432 | Persistent relational store |
| `smi_redis` | `redis:7-alpine` | 6379 | Trending score cache |
| `smi_backend` | Custom (FastAPI) | 8000 | API + scheduler + workers |
| `smi_pipeline` | Same as backend | — | One-shot seed on startup, exits |
| `smi_frontend` | Custom (Next.js) | 3000 | UI server |

### Service Dependencies

```
postgres  (healthcheck: pg_isready)
  └── backend  (waits: postgres healthy)
        └── frontend  (waits: backend started)
redis  (healthcheck: redis-cli ping)
  └── backend  (waits: redis healthy)
pipeline  (waits: postgres healthy)
```

### Environment Variables

| Variable | Consumer | Description |
|----------|----------|-------------|
| `DATABASE_URL` | backend | Async PostgreSQL DSN |
| `REDIS_URL` | backend | Redis connection URL |
| `REDIS_PASSWORD` | backend + redis | Redis auth password |
| `POSTGRES_USER` | postgres + backend | DB username |
| `POSTGRES_PASSWORD` | postgres + backend | DB password |
| `POSTGRES_DB` | postgres + backend | Database name |
| `SECRET_KEY` | backend | App secret (JWT future use) |
| `ALLOWED_ORIGINS` | backend | CORS allowed origin list |
| `NEXT_PUBLIC_API_URL` | frontend | API base URL for Axios |
| `NEWS_API_KEY` | news_ingestor | NewsAPI auth key |
| `ALPHA_VANTAGE_API_KEY` | alphavantage_ingestor | Alpha Vantage auth key |
| `POLYGON_API_KEY` | market_ingestor | Polygon.io auth key |
| `TWITTER_BEARER_TOKEN` | social_ingestor | Twitter API v2 bearer token |
| `REDDIT_CLIENT_ID` | social_ingestor | Reddit OAuth client ID |
| `REDDIT_CLIENT_SECRET` | social_ingestor | Reddit OAuth client secret |
| `REDDIT_USER_AGENT` | social_ingestor | Reddit API user agent string |

### Backup & Recovery

A shell script (`data-pipeline/backup_db.sh`) performs `pg_dump` inside the postgres container and gzips the output to `backups/smi_<timestamp>.sql.gz`. The script retains the 7 most recent dumps and is designed to run via cron on the host or via `docker compose exec`.

To restore:
```bash
gunzip -c backups/smi_<timestamp>.sql.gz | \
  docker compose exec -T postgres psql -U smi_admin_user smi_protected_db
```

---

*Document generated: 2026-05-04 | Version: 0.2.0 | Stage: 5 of 6 complete*
