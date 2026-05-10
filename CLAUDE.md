# Stock Market Indicator — CLAUDE.md

## Project Overview
Sector-Based Trending Ticker & Financial Search Engine. Monorepo with a Next.js frontend, FastAPI backend, Python data-pipeline, PostgreSQL database, and Redis cache.

## Monorepo Layout
```
/
├── frontend/          # Next.js 16 (React 19 + TypeScript + Tailwind 4 + Axios) — port 3000
│   ├── app/
│   │   ├── layout.tsx          # Root layout: Navbar, Toaster, Google Fonts
│   │   ├── page.tsx            # Dashboard: trending list + sector nav + time filter
│   │   └── ticker/[symbol]/
│   │       └── page.tsx        # Ticker detail: sentiment chart + news timeline
│   ├── components/
│   │   ├── dashboard/          # HeroTickers, Navbar, NewsTimeline, SearchPalette,
│   │   │                       # SectorNav, SentimentChart, TickerCard
│   │   └── ui/                 # SentimentBadge, SentimentGauge, SkeletonCard, Sparkline, TimeFilter
│   ├── lib/
│   │   ├── api.ts              # Axios client + typed fetch functions
│   │   └── types.ts            # TrendingTicker, TickerDetail, Article, SearchResult
│   ├── AGENTS.md               # Next.js version warning (read before editing Next.js code)
│   └── Dockerfile
├── backend/           # FastAPI (Python) — port 8000
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── scheduler.py
│   │   ├── models/            # Article (+ minhash_signature, embedding), Ticker, TickerMention (+ source_weight)
│   │   ├── schemas/           # normalized.py, ticker.py, article.py, search.py
│   │   ├── routers/           # ingestors.py, tickers.py, trending.py, search.py, ticker_detail.py
│   │   ├── workers/           # news_ingestor, social_ingestor, market_ingestor,
│   │   │                      # alphavantage_ingestor, yahoo_rss_ingestor, ranking_engine,
│   │   │                      # finnhub_ingestor, gdelt_ingestor, googlenews_ingestor, stocktwits_ingestor
│   │   ├── utils/             # ticker_extractor.py (FinBERT-NER), sentiment.py (FinBERT),
│   │   │                      # embeddings.py (MiniLM), minhash_dedup.py, query_pipeline.py
│   │   └── migrations/        # Alembic env + versions/0001–0004
│   ├── requirements.txt
│   ├── Dockerfile
│   └── alembic.ini
├── data-pipeline/
│   ├── run_ingestors.py        # One-shot bulk ingestion (used by pipeline container)
│   ├── run_ranking.py          # Standalone ranking engine runner (cron alternative)
│   ├── backup_db.sh            # pg_dump → backups/smi_<timestamp>.sql.gz (7-day retention)
│   ├── cron/crontab.example
│   └── seeds/tickers_seed.sql
├── backups/                    # DB dump files (gitignored); created by backup_db.sh
├── docker-compose.yml
├── .env
└── CLAUDE.md
```

## Active Ports
| Service    | Port |
|------------|------|
| Frontend   | 3000 |
| Backend    | 8000 |
| PostgreSQL | 5432 |
| Redis      | 6379 |

## Docker Containers
| Container      | Role |
|----------------|------|
| `smi_backend`  | FastAPI + APScheduler (all cron jobs live here) |
| `smi_postgres` | PostgreSQL data store |
| `smi_redis`    | Redis cache (trending scores) |
| `smi_pipeline` | One-shot bulk seed on startup, then exits |
| `smi_frontend` | Next.js UI |

## Container Restart / Rebuild Guide

> **After editing any file below, run the listed command before testing your changes.**

| Files Changed | Action Required |
|---|---|
| `backend/app/**/*.py` (any Python source) | `docker compose restart backend` |
| `backend/app/migrations/**` (Alembic migrations) | `docker compose restart backend` then `docker exec -it smi_backend alembic upgrade head` |
| `backend/requirements.txt` | `docker compose up -d --build backend` (full rebuild) |
| `backend/Dockerfile` | `docker compose up -d --build backend` (full rebuild) |
| `frontend/**` (any JS/TS/CSS source) | `docker compose up -d --build frontend` (restart may not pick up changes) |
| `frontend/package.json` or `frontend/package-lock.json` | `docker compose up -d --build frontend` (full rebuild) |
| `data-pipeline/**` | `docker compose up --build pipeline` (re-runs one-shot seed) |
| `docker-compose.yml` | `docker compose up -d` (recreates changed services) |
| `.env` | `docker compose up -d` (recreates all services to pick up new env vars) |

**Quick reference:**
```bash
# Restart a single container (code-only changes)
docker compose restart <service>   # service = backend | frontend | redis | postgres | pipeline

# Rebuild + restart a single container (dependency / Dockerfile changes)
docker compose up -d --build <service>

# Rebuild everything from scratch
docker compose down && docker compose up -d --build
```

## Database Schema
| Table | Key Columns |
|---|---|
| `tickers` | `symbol` (PK), `sector`, `company_name` |
| `articles` | `id`, `source`, `title`, `content`, `url`, `timestamp`, `minhash_signature` (JSONB), `embedding` (vector 384) |
| `ticker_mentions` | `id`, `ticker` (FK→tickers), `article_id` (FK→articles), `sentiment`, `source_weight` |

> Time-window queries must filter on `Article.timestamp` (publication time), not `TickerMention.created_at` (ingestion time).

Migrations (run in order): `0001_initial_schema` → `0002_add_minhash_signature` → `0003_add_source_weight_to_ticker_mentions` → `0004_add_vector_embeddings`  
Stamp after first run: `docker exec -it smi_backend alembic stamp head`  
Apply pending: `docker exec -it smi_backend alembic upgrade head`

## API Endpoints

### Ingestion (POST)
| Path | Description |
|---|---|
| `/ingest/news-ingestor` | NewsAPI |
| `/ingest/social-ingestor` | Reddit / Twitter |
| `/ingest/market-ingestor` | Polygon.io |
| `/ingest/alphavantage-ingestor` | Alpha Vantage news & sentiment |
| `/ingest/yahoo-rss-ingestor` | Yahoo Finance RSS (all tickers) |

### Tickers (GET)
| Path | Description |
|---|---|
| `/tickers/` | List all tickers |
| `/ticker/{symbol}` | Profile + trend metrics (`?window=6h|24h|3d|7d`) |
| `/ticker/{symbol}/news` | Recent articles for ticker (`?limit=`, `?window=`) |

### Trending (GET)
All trending endpoints accept `?window=6h|24h|3d|7d` (default `24h`). Redis cache is only used for the `24h` global view; all other windows query Postgres live.

| Path | Description |
|---|---|
| `/trending` | Global top-10 from Redis (24h) or live DB |
| `/trending?sector=AI` | Sector top-10 (keyword or canonical name) |
| `/trending?window=7d` | Live DB query for non-24h windows |
| `/trending/sector/{sector}` | Sector top-10 (path style) |
| `/trending/sectors` | List all cached sector keys |

### Search (GET)
| Path | Description |
|---|---|
| `/search?q=` | Full query pipeline — returns `{query, intent, tickers, news, trend_data}` |

### Other
| Path | Description |
|---|---|
| `POST /tickers/` | Add a ticker |
| `GET /health` | Health check |

## Search Query Pipeline (`utils/query_pipeline.py`)
Classifies query into one of four intents:
- `sector_search` — keyword maps to a sector (e.g. "AI stocks" → Technology)
- `ticker_lookup` — bare or `$`-prefixed symbol (e.g. "AAPL", "$TSLA")
- `news_search` — news/event keywords (earnings, report, announce)
- `general_search` — FTS fallback via `ILIKE` on title + content

Returns `SearchResponse`: `{ query, intent, tickers[{symbol, score, mentions_24h, sentiment}], news[{id, source, title, url, timestamp, sentiment}], trend_data[{symbol, mentions_1h, mentions_24h, sentiment, score, spike}] }`

## Ingestion Workers
| Worker | Source | Cron Schedule | Source Weight | Notes |
|---|---|---|---|---|
| `news_ingestor` | NewsAPI | every 30 min | 0.8 | 5 finance queries × 20 articles |
| `social_ingestor` | Reddit / Twitter | every 15 min | Reddit 0.3, Twitter 0.2 | skips if no credentials |
| `market_ingestor` | Polygon.io | hourly :00 | 0.9 | 50 articles/run |
| `alphavantage_ingestor` | Alpha Vantage | every 6 h | 1.0 | 6 topics × 50; 24 req/day (free cap = 25) |
| `yahoo_rss_ingestor` | Yahoo Finance RSS | hourly :30 | 0.85 | per-ticker RSS, no key, ~500 articles/run |
| `finnhub_ingestor` | Finnhub | every 30 min | 0.9 | financial news + company NER |
| `gdelt_ingestor` | GDELT | hourly | 0.75 | broadcast/print media events |
| `googlenews_ingestor` | Google News RSS | every 30 min | 0.8 | keyword-driven RSS feeds |
| `stocktwits_ingestor` | StockTwits | every 15 min | 0.3 | social sentiment only; excluded from ranking counts |
| `ranking_engine` | — | every minute | — | Z-score scoring → Redis TTL 90 s |

All workers deduplicate by `Article.url` (exact) and MinHash/LSH Jaccard similarity ≥ 0.85 before inserting.

## Ranking Score Formula
```
z_score  = (mentions_1h − μ_hourly) / σ_hourly   # 7-day rolling window; 0.0 if σ = 0
sentiment = weighted_avg(sentiment × source_weight) over last 24 h

score = (z_score × 2) + (mentions_24h × 0.15) + (sentiment × 2) + (price_change_pct × 2)
spike boost: if z_score ≥ 2.0  →  score × 1.3
```
Stocktwits excluded from all mention counts. Redis keys: `trending:global`, `trending:{Sector}` — TTL 90 s

## Utilities
- `utils/ticker_extractor.py` — FinBERT-NER (`dslim/bert-base-NER`) + regex fallback + DB cross-reference; runs in ThreadPoolExecutor
- `utils/sentiment.py` — FinBERT (`ProsusAI/finbert`) → `float` in `[-1.0, 1.0]`; async wrapper via `asyncio.to_thread`
- `utils/embeddings.py` — `all-MiniLM-L6-v2` sentence embeddings (384-dim, unit-normalised); async via ThreadPoolExecutor
- `utils/minhash_dedup.py` — MinHash (128 perms, 3-gram shingles) near-duplicate detection; Jaccard ≥ 0.85 threshold, 24 h window
- `utils/query_pipeline.py` — intent detection + sector/ticker entity extraction; semantic search uses vector cosine distance

## Environment Variables
| Variable | Used by |
|---|---|
| `DATABASE_URL` | backend |
| `REDIS_URL` / `REDIS_PASSWORD` | backend |
| `POSTGRES_USER / PASSWORD / DB` | postgres |
| `SECRET_KEY` | backend |
| `ALLOWED_ORIGINS` | backend CORS |
| `NEXT_PUBLIC_API_URL` | frontend |
| `ALPHA_VANTAGE_API_KEY` | alphavantage_ingestor |
| `POLYGON_API_KEY` | market_ingestor |
| `NEWS_API_KEY` | news_ingestor |
| `REDDIT_CLIENT_ID / SECRET / USER_AGENT` | social_ingestor |
| `TWITTER_BEARER_TOKEN` | social_ingestor (requires paid credits) |

## Frontend Tech Stack
| Package | Version | Purpose |
|---|---|---|
| Next.js | 16.2.4 | Framework (App Router) |
| React | 19.2.4 | UI runtime |
| Tailwind CSS | 4.x | Styling |
| framer-motion | 12.x | Animations |
| recharts | 3.x | Sentiment chart |
| lucide-react | 1.x | Icons |
| axios | 1.x | API client |
| react-hot-toast | 2.x | Toast notifications |

> **Note:** Next.js 16 / React 19 have breaking changes from previous versions. Read `frontend/AGENTS.md` before editing any Next.js-specific code.

## Frontend Routes
| Route | Component | Description |
|---|---|---|
| `/` | `app/page.tsx` | Trending dashboard — sector nav, time filter, ticker grid |
| `/ticker/[symbol]` | `app/ticker/[symbol]/page.tsx` | Ticker detail — sentiment chart + news timeline |

Both routes support `?window=6h|24h|3d|7d` and `?sector=<name>` as URL search params (synced to UI state via `router.replace`).

## Backup & Restore
```bash
# Dump DB → backups/smi_<timestamp>.sql.gz (keeps 7 days)
./data-pipeline/backup_db.sh

# Restore
gunzip -c backups/smi_<timestamp>.sql.gz | \
  docker compose exec -T postgres psql -U smi_admin_user smi_protected_db
```

## Useful Commands
```bash
# Check article counts by source
docker compose exec postgres psql -U smi_admin_user -d smi_protected_db \
  -c "SELECT source, COUNT(*) FROM articles GROUP BY source ORDER BY count DESC;"

# Watch live logs
docker compose logs backend -f

# Restart backend (required after scheduler changes)
docker compose restart backend

# Run ranking engine once (outside Docker, uses backend/.venv)
python3 data-pipeline/run_ranking.py
```

## Stage Completion
- [x] Stage 1 — Scaffold, docker-compose, .env.example, CLAUDE.md
- [x] Stage 2 — FastAPI backend, DB schema, ingestion workers, sentiment, ticker extraction, cron scheduling
- [x] Stage 3 — Additional ingestors (Alpha Vantage, Yahoo RSS), URL deduplication, query pipeline
- [x] Stage 4 — Search engine + FastAPI endpoints (trending, search, ticker detail)
- [x] Stage 5 — Frontend (Next.js) UI — dashboard, ticker detail, sentiment chart, news timeline
- [x] Stage 6 (v2) — MinHash/LSH dedup, FinBERT-NER ticker extraction, FinBERT sentiment, Source Authority Matrix, Z-Score anomaly detection, vector embeddings & semantic search, 4 new ingestors (Finnhub, GDELT, Google News, StockTwits)
- [ ] Stage 7 — Integration, testing, deployment polish
