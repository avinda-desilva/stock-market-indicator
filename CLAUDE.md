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
│   │   └── ui/                 # SentimentBadge, SkeletonCard, Sparkline, TimeFilter
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
│   │   ├── models/            # Article, Ticker, TickerMention
│   │   ├── schemas/           # normalized.py, ticker.py, article.py, search.py
│   │   ├── routers/           # ingestors.py, tickers.py, trending.py, search.py, ticker_detail.py
│   │   ├── workers/           # news_ingestor, social_ingestor, market_ingestor,
│   │   │                      # alphavantage_ingestor, yahoo_rss_ingestor, ranking_engine
│   │   ├── utils/             # ticker_extractor.py, sentiment.py, query_pipeline.py
│   │   └── migrations/        # Alembic env + versions/0001_initial_schema
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
| `articles` | `id`, `source`, `title`, `content`, `url`, `timestamp` |
| `ticker_mentions` | `id`, `ticker` (FK→tickers), `article_id` (FK→articles), `sentiment` |

> Time-window queries must filter on `Article.timestamp` (publication time), not `TickerMention.created_at` (ingestion time).

Migration: `app/migrations/versions/0001_initial_schema.py`  
Stamp after first run: `docker exec -it smi_backend alembic stamp head`

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
| Worker | Source | Cron Schedule | Notes |
|---|---|---|---|
| `news_ingestor` | NewsAPI | every 30 min | 5 finance queries × 20 articles |
| `social_ingestor` | Reddit + Twitter | every 15 min | skips if no credentials |
| `market_ingestor` | Polygon.io | hourly :00 | 50 articles/run |
| `alphavantage_ingestor` | Alpha Vantage | every 6 h | 6 topics × 50; 24 req/day (free cap = 25) |
| `yahoo_rss_ingestor` | Yahoo Finance RSS | hourly :30 | per-ticker RSS, no key, ~500 articles/run |
| `ranking_engine` | — | every minute | scores tickers → Redis TTL 90 s |

All workers deduplicate by `Article.url` before inserting.

## Ranking Score Formula
```
score = (mentions_1h × 3) + (mentions_24h × 1.5) + (sentiment × 2) + (price_change_pct × 2)
spike boost: if mentions_1h > (mentions_24h / 24) × 2  →  score × 1.5
```
Redis keys: `trending:global`, `trending:{Sector}` — TTL 90 s

## Utilities
- `utils/ticker_extractor.py` — regex + stopword filter + DB dictionary match
- `utils/sentiment.py` — TextBlob polarity → `float` in `[-1.0, 1.0]`
- `utils/query_pipeline.py` — intent detection + sector/ticker entity extraction

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
- [x] Stage 3 — Additional ingestors (Alpha Vantage, Yahoo RSS), deduplication, query pipeline
- [x] Stage 4 — Search engine + FastAPI endpoints (trending, search, ticker detail)
- [x] Stage 5 — Frontend (Next.js) UI — dashboard, ticker detail, sentiment chart, news timeline
- [ ] Stage 6 — Integration, testing, deployment polish
