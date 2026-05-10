export interface TrendingTicker {
  symbol: string;
  company_name?: string;
  sector?: string;
  mentions_1h: number;
  mentions_24h: number;
  sentiment: number;
  score: number;
  spike: boolean;
}

export interface TickerDetail {
  symbol: string;
  company_name: string;
  sector: string;
  mentions_1h: number;
  mentions_24h: number;
  sentiment: number;
  score: number;
  spike: boolean;
  gauge_sentiment_15m: number | null;
}

export interface Article {
  id: number;
  source: string;
  title: string;
  url: string;
  timestamp: string;
  sentiment?: number;
}

export interface SearchResult {
  query: string;
  intent: string;
  tickers: Array<{
    symbol: string;
    score: number;
    mentions_24h: number;
    sentiment: number;
  }>;
  news: Article[];
  trend_data: TrendingTicker[];
}
