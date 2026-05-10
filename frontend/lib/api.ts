import axios from "axios";
import type { TrendingTicker, TickerDetail, Article, SearchResult } from "./types";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  timeout: 10000,
});

export async function getTrending(sector?: string, window = "24h"): Promise<TrendingTicker[]> {
  const params: Record<string, string> = { window };
  if (sector) params.sector = sector;
  const { data } = await api.get("/trending", { params });
  return data;
}

export async function getTrendingSectors(): Promise<string[]> {
  const { data } = await api.get("/trending/sectors");
  return data.sectors ?? data;
}

export async function getTickerDetail(symbol: string, window = "24h"): Promise<TickerDetail> {
  const { data } = await api.get(`/ticker/${symbol}`, { params: { window } });
  return data;
}

const WINDOW_LIMITS: Record<string, number> = { "6h": 50, "24h": 50, "3d": 200, "7d": 200 };

export async function getTickerNews(symbol: string, limit?: number, window = "24h"): Promise<Article[]> {
  const resolvedLimit = limit ?? WINDOW_LIMITS[window] ?? 50;
  const { data } = await api.get(`/ticker/${symbol}/news`, { params: { limit: resolvedLimit, window } });
  return data.news ?? data;
}

export async function searchTickers(query: string): Promise<SearchResult> {
  const { data } = await api.get("/search", { params: { q: query } });
  return data;
}

export async function triggerAllIngestors(): Promise<void> {
  await api.post("/ingest/all");
}
