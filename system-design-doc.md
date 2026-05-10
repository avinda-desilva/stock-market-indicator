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

**Stock Market Indicator (SMI)** is a sector-based trending ticker and financial search engine. It continuously ingests articles and social posts from nine distinct data sources, extracts ticker mentions via FinBERT-NER, scores each ticker using a Z-score anomaly detection formula weighted by source authority, and presents a live-updating dashboard with sector filtering, time-window controls, and a semantic vector search engine.

The application is organized as three cooperating systems:

| # | System | Responsibility |
|---|--------|---------------|
| 1 | Data Pipeline | Ingest, normalize, MinHash deduplicate, embed, and store articles; seed the ticker dictionary |
| 2 | Backend API | Persist data, run the Z-score ranking engine, serve REST endpoints, maintain Redis cache |
| 3 | Frontend UI | Real-time dashboard, sector navigation, ticker detail pages, semantic search palette |

---

## 3. System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              External Data Sources                            │
│  NewsAPI │ Alpha Vantage │ Polygon.io │ Twitter/Reddit │ Yahoo RSS            │
│  Finnhub │ GDELT         │ Google News RSS             │ StockTwits           │
└─────┬───────────────┬──────────────┬─────────────────┬────────────────────────┘
      │               │              │                 │
      ▼               ▼              ▼                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                 System 1 — Data Pipeline (APScheduler)                        │
│                                                                               │
│  news_ingestor │ alphavantage_ingestor │ market_ingestor │ finnhub_ingestor   │
│  social_ingestor │ yahoo_rss_ingestor  │ gdelt_ingestor  │ googlenews_ingestor│
│  stocktwits_ingestor │ ranking_engine                                         │
│                                                                               │
│  Normalize → FinBERT Sentiment → FinBERT-NER Ticker Extraction               │
│  → MinHash Dedup → Embed (MiniLM) → Source Weight → Persist                  │
└────────────────────────────────┬────────────────────────────────────────────┘
                                │ SQLAlchemy async writes
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                   PostgreSQL + pgvector  (smi_postgres)            │
│   tickers  │  articles (minhash_signature, embedding)              │
│   ticker_mentions (source_weight)                                  │
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

Each worker follows the same contract: accept an `AsyncSession`, fetch data from its source, normalize into a `NormalizedItem`, run FinBERT sentiment and FinBERT-NER ticker extraction, MinHash-deduplicate, generate a sentence embedding, set a source authority weight, persist to PostgreSQL, and commit.

#### news_ingestor — NewsAPI
- **Source:** `https://newsapi.org/v2/everything`
- **Schedule:** Every 30 minutes
- **Volume:** 5 finance queries × 20 articles per query = up to 100 articles/run
- **Queries:** `["stock market", "NYSE", "NASDAQ", "earnings", "IPO"]`
- **Key required:** `NEWS_API_KEY`
- **Source weight:** 0.8
- **Process:** Fetches article headline + content → FinBERT sentiment → FinBERT-NER ticker extraction → MinHash dedup → embed → upsert

#### alphavantage_ingestor — Alpha Vantage
- **Source:** `https://www.alphavantage.co/query?function=NEWS_SENTIMENT`
- **Schedule:** Every 6 hours
- **Volume:** 6 sector topics × 50 articles = up to 300 articles/run (24 requests/day, within 25/day free cap)
- **Topics:** `technology`, `finance`, `energy_transportation`, `manufacturing`, `real_estate`, `retail_wholesale`
- **Key required:** `ALPHA_VANTAGE_API_KEY`
- **Source weight:** 1.0 (highest authority — pre-computed financial sentiment)
- **Special behavior:** AV provides its own `overall_sentiment_score` and per-ticker `ticker_sentiment_score`. These float scores are preferred over FinBERT when available. The worker merges AV-supplied ticker symbols with our DB-verified NER extractor.

#### market_ingestor — Polygon.io
- **Source:** `https://api.polygon.io/v2/reference/news`
- **Schedule:** Hourly at :00
- **Volume:** 50 articles/run
- **Key required:** `POLYGON_API_KEY`
- **Source weight:** 0.9
- **Process:** Polygon returns article-level ticker arrays; these are cross-validated against the DB ticker dictionary before being saved as `TickerMention` rows.

#### social_ingestor — Twitter/X & Reddit
- **Source:**
  - Twitter: `https://api.twitter.com/2/tweets/search/recent`
  - Reddit: PRAW library, subreddits `r/stocks`, `r/wallstreetbets`, `r/investing`, `r/StockMarket`
- **Schedule:** Every 15 minutes
- **Volume:** 3 queries × 20 tweets + 4 subreddits × 25 posts
- **Keys required:** `TWITTER_BEARER_TOKEN` (paid tier), `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`
- **Source weight:** Reddit 0.3, Twitter 0.2 (social signals carry lower authority than curated news)
- **Behavior:** Gracefully skips if credentials are absent. Twitter queries filter language to English and exclude retweets.

#### yahoo_rss_ingestor — Yahoo Finance RSS
- **Source:** `https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}`
- **Schedule:** Hourly at :30
- **Volume:** ~50 tickers × 15 items/feed = ~750 article candidates/run, minus duplicates
- **Key required:** None (public RSS feed)
- **Source weight:** 0.85
- **Process:** Fetches all tracked ticker symbols concurrently (capped at 5 simultaneous requests via asyncio Semaphore). Parses XML with stdlib `xml.etree`. Each feed item is inherently tied to a known ticker, so no NER extraction is needed for ticker association.

#### finnhub_ingestor — Finnhub
- **Source:** `https://finnhub.io/api/v1/news`
- **Schedule:** Every 30 minutes
- **Key required:** `FINNHUB_API_KEY`
- **Source weight:** 0.9
- **Process:** Fetches general financial news; FinBERT-NER extracts tickers from article text. High-quality curated source, weight near Polygon.

#### gdelt_ingestor — GDELT
- **Source:** GDELT GKG / Event API
- **Schedule:** Hourly
- **Key required:** None (public)
- **Source weight:** 0.75
- **Process:** Ingests broadcast/print media events with financial relevance signals. Lower authority weight due to broad source coverage including unvetted outlets.

#### googlenews_ingestor — Google News RSS
- **Source:** `https://news.google.com/rss/search?q={query}`
- **Schedule:** Every 30 minutes
- **Key required:** None (public RSS)
- **Source weight:** 0.8
- **Process:** Finance-keyword-driven RSS feeds; FinBERT-NER ticker extraction. Comparable authority to NewsAPI.

#### stocktwits_ingestor — StockTwits
- **Source:** StockTwits stream API
- **Schedule:** Every 15 minutes
- **Key required:** None (public)
- **Source weight:** 0.3
- **Process:** Social sentiment only; FinBERT scores posts. StockTwits mentions are **excluded from all ranking mention counts** — they inflate volume without contributing to the news timeline. Used only for supplemental sentiment signal.

### 4.2 Ticker Extraction (`utils/ticker_extractor.py`)

Uses a **FinBERT-NER pipeline** (`dslim/bert-base-NER`) loaded once at startup as a module-level singleton. NER inference runs in a shared `ThreadPoolExecutor` so async callers are never blocked.

**Pipeline:**
1. Text is truncated to 1,500 chars (~512 tokens) before passing to the NER model
2. `dslim/bert-base-NER` tags named entities; `ORG` entities are extracted as candidate ticker names
3. Candidates are uppercased and cross-referenced against the `tickers` table in PostgreSQL — only DB-confirmed symbols are kept
4. **Fallback:** If the NER model is unavailable (import error or OOM), the extractor falls back to regex `\$?([A-Z]{2,5})\b` + an expanded stopword filter

### 4.3 Sentiment Analysis (`utils/sentiment.py`)

All text-level sentiment uses **FinBERT** (`ProsusAI/finbert`):
- Domain-specific financial language model; outperforms TextBlob on earnings/analyst/market text
- Returns a `float` in `[-1.0, 1.0]`: positive confidence → positive score, negative confidence → negative score, neutral → 0.0
- Text is pre-trimmed to 2,000 chars to bound tokenizer input
- Exposed as both a sync (`analyze_sentiment`) and async (`analyze_sentiment_async`) interface via `asyncio.to_thread`
- Alpha Vantage articles may still override with their own pre-computed float score

### 4.4 Deduplication

Two-layer deduplication strategy:

**Layer 1 — Exact URL match** (unchanged):
1. Collect candidate URLs in the current batch
2. `SELECT url FROM articles WHERE url IN (...)` in one round trip
3. Skip exact-URL duplicates and within-batch duplicates via `seen_urls` set

**Layer 2 — MinHash/LSH near-duplicate detection** (`utils/minhash_dedup.py`):
1. Compute a **128-permutation MinHash** signature from 3-gram word shingles of the article content
2. Load signatures of all articles ingested within the last 24 hours from `articles.minhash_signature` (JSONB column)
3. Estimate **Jaccard similarity** between the candidate and each recent signature
4. If any similarity ≥ **0.85**, the article is treated as a near-duplicate and skipped — catches syndicated copies, paraphrase rewrites, and RSS/API source overlap
5. The MinHash signature is stored in `articles.minhash_signature` (JSONB array of 128 integers)

**Tuning constants:** `NUM_PERM=128`, `SIMILARITY_THRESHOLD=0.85`, `DEDUP_WINDOW_HOURS=24`, `MIN_CONTENT_TOKENS=5`

### 4.4a Vector Embeddings (`utils/embeddings.py`)

After deduplication, each article's `"{title} {content}"` is encoded into a **384-dimensional unit-normalised embedding vector** using `sentence-transformers/all-MiniLM-L6-v2`:
- Model loaded once as a module-level singleton via `@lru_cache`
- Inference runs in a dedicated `ThreadPoolExecutor` (2 workers) so the asyncio loop is never blocked
- Vectors stored in `articles.embedding` (pgvector `VECTOR(384)` column)
- Used by the search router for cosine-distance semantic retrieval

### 4.5 Source Authority Matrix

Each `TickerMention` row carries a `source_weight` float (0.0–1.0) set by the ingestor at write time. The ranking engine uses these weights to compute a **weighted-average sentiment** rather than a simple mean, so high-authority sources (Alpha Vantage, Polygon, Finnhub) exert more influence on a ticker's sentiment score than social feeds.

| Source | Weight | Rationale |
|--------|--------|-----------|
| Alpha Vantage | 1.0 | Pre-computed financial NLP, curated feed |
| Polygon.io | 0.9 | Professional market data provider |
| Finnhub | 0.9 | Curated financial news |
| Yahoo Finance RSS | 0.85 | Reputable financial outlet, per-ticker feed |
| NewsAPI | 0.8 | General news — quality varies by outlet |
| Google News RSS | 0.8 | Broad aggregator — quality varies |
| GDELT | 0.75 | Broadcast/print; includes unvetted sources |
| Reddit | 0.3 | Social opinion — low editorial control |
| StockTwits | 0.3 | Social sentiment only |
| Twitter/X | 0.2 | Noisiest signal; unfiltered public posts |

### 4.6 Ranking Engine

- **Schedule:** Every minute
- **Storage:** Redis keys `trending:global` and `trending:{Sector}`, TTL = 90 seconds

The ranking engine scores every ticker in the database each minute and writes the results to Redis. See [Section 11](#11-ranking-algorithm) for the formula.

### 4.7 Nightly Cleanup

A cleanup job runs at 23:59 UTC daily. It deletes `Article` rows (and cascades to `TickerMention`) older than 7 days, keeping the database from growing unbounded.

### 4.8 One-Shot Seed Pipeline (`smi_pipeline` container)

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
| `Article` | `articles` | `id`, `source`, `title`, `content`, `url`, `timestamp`, `minhash_signature` (JSONB), `embedding` (Vector 384) |
| `TickerMention` | `ticker_mentions` | `id`, `ticker` (FK → tickers), `article_id` (FK → articles), `sentiment`, `source_weight` |

Time-window queries always filter on `Article.timestamp` (publication time), not `TickerMention.created_at` (ingestion time). This ensures that a 6-hour window reflects when news was published, not when SMI happened to process it.

### 5.4 Database — PostgreSQL + pgvector

- Image: `postgres:15-alpine` + `pgvector` extension
- Async driver: `asyncpg`
- ORM: SQLAlchemy 2.0 (async session pattern)
- Migrations: Alembic (`0001_initial_schema` → `0002_add_minhash_signature` → `0003_add_source_weight_to_ticker_mentions` → `0004_add_vector_embeddings`)
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
| `finnhub_ingestor` | `*/30 * * * *` | `_run_finnhub()` |
| `gdelt_ingestor` | `0 * * * *` | `_run_gdelt()` |
| `googlenews_ingestor` | `*/30 * * * *` | `_run_googlenews()` |
| `stocktwits_ingestor` | `*/15 * * * *` | `_run_stocktwits()` |
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
| pgvector | 0.3.x | PostgreSQL vector similarity extension + SQLAlchemy type |
| Redis (redis.asyncio) | 5.2.1 | Async Redis client |
| Pydantic | 2.11.4 | Data validation + settings |
| pydantic-settings | 2.9.1 | `.env` config management |
| httpx | 0.28.1 | Async HTTP client for external APIs |
| transformers | 4.x | FinBERT sentiment (`ProsusAI/finbert`) + FinBERT-NER (`dslim/bert-base-NER`) |
| sentence-transformers | 3.x | `all-MiniLM-L6-v2` 384-dim embeddings |
| datasketch | 1.6.x | MinHash / LSH near-duplicate detection |
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
| Database | PostgreSQL + pgvector | 15-alpine |
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
- **How it's used:** All ticker symbols in the DB are iterated. Feeds are fetched concurrently (max 5 simultaneous). Since the feed is per-ticker, no NER extraction is needed — the ticker is known from the feed URL. Each item's headline + description is FinBERT-scored.
- **Volume:** ~50 tickers × 15 items = ~750 candidates/run (after deduplication, much less)

### Finnhub
- **Variable:** `FINNHUB_API_KEY`
- **Endpoint:** `https://finnhub.io/api/v1/news`
- **What it provides:** Curated financial news with company coverage
- **How it's used:** Fetched every 30 minutes; FinBERT-NER extracts ticker mentions from article text. High source weight (0.9) due to curated feed quality.
- **Rate limit:** Free tier: 60 API calls/minute

### GDELT
- **No API key required**
- **What it provides:** Global news event database including broadcast, print, and web media with financial relevance signals
- **How it's used:** Fetched hourly; lower source weight (0.75) due to broad, unvetted source coverage. FinBERT-NER extracts tickers.

### Google News RSS
- **No API key required**
- **Endpoint:** `https://news.google.com/rss/search?q={query}`
- **What it provides:** Aggregated financial news from diverse outlets
- **How it's used:** Finance-keyword-driven RSS queries every 30 minutes; FinBERT-NER ticker extraction.

### StockTwits
- **No API key required (public stream)**
- **What it provides:** Real-time social sentiment from retail investors; posts are typically short, ticker-tagged messages
- **How it's used:** Fetched every 15 minutes. FinBERT scores posts. **Excluded from all ranking mention counts** to prevent social volume inflation; used only for supplemental weighted sentiment signal. Source weight: 0.3.

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
   analyze_sentiment_async("{title} {content}")
   → FinBERT("ProsusAI/finbert") → label="positive", confidence=0.91
   → polarity = +0.91  ← stored in NormalizedItem.sentiment

4. TICKER EXTRACTION
   extract_tickers_db("Apple beats Q3 earnings expectations Apple Inc. reported...", db)
   → dslim/bert-base-NER tags: [("Apple", ORG), ("Apple Inc.", ORG)]
   → candidates uppercased: ["APPLE"]
   → DB lookup: SELECT symbol FROM tickers WHERE symbol IN ('APPLE')
   → cross-reference returns []  (no match for "APPLE")
   → separately NER / regex finds "AAPL" if present in text
   → returns ["AAPL"]

5. DEDUPLICATE — Layer 1 (exact URL)
   SELECT url FROM articles WHERE url = 'https://...'
   → empty result → not an exact duplicate, continue

   DEDUPLICATE — Layer 2 (MinHash/LSH)
   sig = compute_minhash("{title} {content}")  # 128-perm MinHash on 3-gram shingles
   recent_sigs = await load_recent_signatures(db)  # last 24 h
   is_near_duplicate(sig, recent_sigs, threshold=0.85) → False → proceed

6. EMBED
   embedding = await encode("{title} {content}")
   → all-MiniLM-L6-v2 → 384-dim unit-normalised vector

7. PERSIST
   INSERT INTO articles (source, title, content, url, timestamp, minhash_signature, embedding)
   VALUES (..., [sig_ints], [vec_floats])
   → article.id = 9821

   INSERT INTO ticker_mentions (ticker, article_id, sentiment, source_weight)
   VALUES ('AAPL', 9821, 0.91, 0.8)  ← source_weight=0.8 for newsapi

8. COMMIT
   db.commit()

9. RANKING ENGINE (runs every minute)
   Z-score SQL CTE:
   - Bucket TickerMentions into 1h slots over the last 7 days (excluding stocktwits)
   - Compute per-ticker μ and σ across those hourly buckets
   - Isolate the current hour bucket → mentions_1h
   - Compute z_score = (mentions_1h - μ) / σ

   → z_score(NVDA) = (21 - 5.96) / 7.5 = 2.01

   Weighted sentiment:
   → weighted_sentiment(NVDA) = SUM(sentiment × source_weight) / SUM(source_weight)
                               = 0.58 (over last 24 h)

   score(NVDA) = (2.01 × 2) + (143 × 0.15) + (0.58 × 2) + (0.0 × 2)
              = 4.02 + 21.45 + 1.16 + 0
              = 26.63

   spike_check: z_score 2.01 ≥ 2.0 → spike boost
   score(NVDA) = 26.63 × 1.3 = 34.62

10. REDIS WRITE
    SET trending:global '[{"symbol":"NVDA","score":34.62,"z_score":2.01,"spike":true,...}, ...]' EX 90
    SET trending:Technology '[{"symbol":"NVDA","score":34.62,...}, ...]'  EX 90

11. API SERVE
    GET /trending → reads trending:global from Redis → returns JSON array

12. FRONTEND RENDER
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
| `source` | VARCHAR | `newsapi`, `alphavantage`, `polygon`, `twitter`, `reddit`, `yahoo_rss`, `finnhub`, `gdelt`, `googlenews`, `stocktwits` |
| `title` | TEXT | Nullable |
| `content` | TEXT | Body or description |
| `url` | TEXT (UNIQUE) | Exact-match deduplication key |
| `timestamp` | TIMESTAMPTZ | Publication time (used for all window filters) |
| `created_at` | TIMESTAMPTZ | Ingestion time (not used for window filters) |
| `minhash_signature` | JSONB | 128-integer MinHash signature for near-duplicate detection |
| `embedding` | VECTOR(384) | all-MiniLM-L6-v2 unit-normalised embedding for semantic search |

### `ticker_mentions`
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER (PK, auto) | |
| `ticker` | VARCHAR (FK → tickers.symbol) | |
| `article_id` | INTEGER (FK → articles.id, CASCADE DELETE) | |
| `sentiment` | FLOAT | FinBERT polarity score in `[-1.0, 1.0]` |
| `source_weight` | FLOAT | Authority weight set by ingestor (0.0–1.0); default 1.0 |
| `created_at` | TIMESTAMPTZ | Ingestion time |

> **Design note:** `Article.timestamp` is the article's publication time. `TickerMention.created_at` (ingestion time) is intentionally excluded from all window-filter queries. This means the `24h` window shows articles *published* in the last 24 hours, not articles *ingested* in that period.

---

## 11. Ranking Algorithm

The ranking engine runs every minute and uses **Z-score anomaly detection** to surface relative spikes rather than raw volume. All mention counts exclude StockTwits articles.

### 11.1 Z-Score Calculation

A PostgreSQL CTE buckets all non-StockTwits `TickerMention` rows into 1-hour slots over the last 7 days, computes per-ticker `μ` (mean hourly mentions) and `σ` (standard deviation), then derives the current hour's Z-score:

```
z_score = (mentions_1h − μ_hourly) / σ_hourly
```

Falls back to 0.0 when `σ = 0` or the ticker has no 7-day history.

### 11.2 Score Formula

```
weighted_sentiment = SUM(sentiment × source_weight) / SUM(source_weight)  [last 24 h]

score = (z_score × 2) + (mentions_24h × 0.15) + (weighted_sentiment × 2) + (price_change_pct × 2)
```

**Spike boost:** If `z_score ≥ 2.0` (2 σ above mean):
```
score = score × 1.3
```

**Weight rationale:**
- `z_score × 2` — primary signal; detects relative velocity spikes even for low-volume tickers
- `mentions_24h × 0.15` — volume anchor; keeps established high-mention tickers visible during quiet hours
- `weighted_sentiment × 2` — source-authority-weighted sentiment lifts/suppresses score
- `price_change_pct × 2` — reserved for market data integration (currently 0.0)
- Spike threshold of 2.0 σ with a conservative 1.3× boost (reduced from 1.5× to avoid over-amplification)

**Output per ranked ticker:** `{symbol, sector, company_name, score, mentions_1h, mentions_24h, sentiment, price_change_pct, z_score, spike}`

**Redis keys:**
- `trending:global` — top-10 across all sectors, sorted by score descending
- `trending:{Sector}` — top-10 per sector (e.g. `trending:Technology`)
- TTL: **90 seconds** per key

---

## 12. Search Query Pipeline

The search system (`utils/query_pipeline.py` + `routers/search.py`) processes free-text queries into typed, intent-driven responses. News retrieval uses **semantic vector search** backed by pgvector.

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
| `news_search` | "earnings report", "beat estimates" | Semantic cosine search on article embeddings |
| `general_search` | "cloud computing growth" | Semantic cosine search; ILIKE FTS fallback on cold start |

### Semantic Search (`_vector_articles`)

For `news_search` and `general_search` intents:
1. The query string is encoded with `all-MiniLM-L6-v2` via `utils/embeddings.encode()`
2. pgvector cosine distance query retrieves nearest articles: `ORDER BY embedding <=> query_vec`
3. Articles without embeddings (cold-start) fall through to an `ILIKE` keyword fallback
4. Results are de-duplicated by URL via `DISTINCT ON` before final re-sort

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

*Document updated: 2026-05-10 | Version: 0.3.0 | Stage: 6 (v2 features) complete*
