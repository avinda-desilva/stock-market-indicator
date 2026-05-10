# Stock Market Indicator (SMI)

A sector-based trending ticker and financial search engine for retail investors and analysts.

## Problem

Financial markets generate enormous volumes of news, social posts, and market data every minute. No single free tool:
- Aggregates content from multiple heterogeneous sources in real time
- Identifies which tickers are gaining *abnormal* attention vs. their rolling baseline
- Quantifies sentiment direction at the ticker and sector level
- Supports natural-language queries like "AI stocks" or "TSLA earnings"

## Solution

SMI continuously ingests articles and social posts from **9 data sources**, extracts ticker mentions via FinBERT-NER, scores each ticker using a Z-score anomaly detection formula weighted by source authority, and presents a live dashboard with sector filtering, time-window controls, and a semantic vector search engine.

```
score = (z_score × 2) + (mentions_24h × 0.15) + (weighted_sentiment × 2) + (price_change_pct × 2)
spike boost: z_score ≥ 2σ → score × 1.3
```

## Architecture

| Layer | Tech | Role |
|---|---|---|
| Data Pipeline | APScheduler + FinBERT + MiniLM | Ingest, deduplicate, embed, rank |
| Backend API | FastAPI + PostgreSQL + Redis | Persist, score, serve REST endpoints |
| Frontend UI | Next.js 16 + React 19 + Tailwind 4 | Dashboard, search, ticker detail |

All services run in Docker Compose. PostgreSQL stores articles with pgvector embeddings (384-dim) for semantic search. Redis caches trending scores (90s TTL).

## Data Sources

NewsAPI · Alpha Vantage · Polygon.io · Yahoo Finance RSS · Finnhub · GDELT · Google News RSS · Reddit · StockTwits

## Quick Start

```bash
cp .env.example .env   # fill in API keys
docker compose up -d --build
```

Frontend → `http://localhost:3000` | API → `http://localhost:8000`

See [system-design-doc.md](./system-design-doc.md) for full architecture, schema, and ranking algorithm details.
