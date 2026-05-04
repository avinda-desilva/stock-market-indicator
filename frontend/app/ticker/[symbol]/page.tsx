"use client";

import { useState, useEffect, useCallback, use, useRef, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowLeft, Newspaper, Clock } from "lucide-react";
import toast from "react-hot-toast";
import { getTickerDetail, getTickerNews } from "@/lib/api";
import type { TickerDetail, Article } from "@/lib/types";
import { SentimentBadge } from "@/components/ui/SentimentBadge";
import { SentimentChart, type ChartPoint } from "@/components/dashboard/SentimentChart";
import { NewsTimeline } from "@/components/dashboard/NewsTimeline";
import { TimeFilter, type TimeWindow } from "@/components/ui/TimeFilter";

const WINDOW_LABELS: Record<TimeWindow, string> = {
  "6h": "6h",
  "24h": "24h",
  "3d": "3d",
  "7d": "7d",
};

// Bucket size in ms per window
const BUCKET_MS: Record<TimeWindow, number> = {
  "6h": 30 * 60 * 1000,
  "24h": 2 * 60 * 60 * 1000,
  "3d": 6 * 60 * 60 * 1000,
  "7d": 12 * 60 * 60 * 1000,
};

function deduplicateArticles(articles: Article[]): Article[] {
  const seen = new Set<string>();
  return articles.filter((a) => {
    const key = a.title?.toLowerCase().trim() ?? String(a.id);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function buildChartData(articles: Article[], window: TimeWindow): ChartPoint[] {
  const sorted = [...articles].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );
  if (!sorted.length) return [];

  const bucketMs = BUCKET_MS[window];
  const buckets = new Map<number, { sentiments: number[]; articleId: number; time: string }>();

  for (const a of sorted) {
    const ts = new Date(a.timestamp).getTime();
    const bucketKey = Math.floor(ts / bucketMs) * bucketMs;
    if (!buckets.has(bucketKey)) {
      const d = new Date(bucketKey);
      buckets.set(bucketKey, {
        sentiments: [],
        articleId: a.id,
        time: `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:00`,
      });
    }
    buckets.get(bucketKey)!.sentiments.push(a.sentiment ?? 0);
  }

  return Array.from(buckets.entries())
    .sort(([a], [b]) => a - b)
    .map(([bucketMs, b]) => ({
      articleId: b.articleId,
      time: b.time,
      ts: bucketMs,
      sentiment: b.sentiments.reduce((s, v) => s + v, 0) / b.sentiments.length,
      mentions: b.sentiments.length,
    }));
}

// Static identity fields — set once on first successful load, never cleared on refetch.
interface StaticIdentity {
  symbol: string;
  company_name: string | null;
}

function TickerContent({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = use(params);
  const searchParams = useSearchParams();
  const router = useRouter();

  const initialWindow = (searchParams.get("window") as TimeWindow) || "24h";

  const [activeWindow, setActiveWindow] = useState<TimeWindow>(initialWindow);
  // detail holds the full API response; staticIdentity holds symbol+name and never resets.
  const [detail, setDetail] = useState<TickerDetail | null>(null);
  const [staticIdentity, setStaticIdentity] = useState<StaticIdentity | null>(null);
  const [articles, setArticles] = useState<Article[]>([]);
  // transitioning: true while a window-switch fetch is in-flight (not first load).
  const [transitioning, setTransitioning] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [newsLoading, setNewsLoading] = useState(true);
  const [highlightedId, setHighlightedId] = useState<number | null>(null);
  const newsScrollRef = useRef<HTMLDivElement>(null);
  const newsContainerRef = useRef<HTMLDivElement>(null);
  const [newsMaxHeight, setNewsMaxHeight] = useState("480px");
  const isFirstLoad = useRef(true);

  const fetchData = useCallback(async (window: TimeWindow) => {
    const firstLoad = isFirstLoad.current;
    if (firstLoad) {
      setInitialLoading(true);
      setNewsLoading(true);
    } else {
      setTransitioning(true);
      setNewsLoading(true);
    }
    setHighlightedId(null);
    try {
      const [d, a] = await Promise.all([
        getTickerDetail(symbol, window),
        getTickerNews(symbol, 50, window),
      ]);
      setDetail(d);
      // Preserve static identity after first successful response.
      if (d && isFirstLoad.current) {
        setStaticIdentity({ symbol: d.symbol, company_name: d.company_name ?? null });
        isFirstLoad.current = false;
      }
      const unique = deduplicateArticles([...a]);
      setArticles(unique.sort((x, y) => new Date(y.timestamp).getTime() - new Date(x.timestamp).getTime()));
    } catch {
      toast.error(`Failed to load data for ${symbol}`);
    } finally {
      setInitialLoading(false);
      setTransitioning(false);
      setNewsLoading(false);
    }
  }, [symbol]);

  useEffect(() => { fetchData(initialWindow); }, [fetchData, initialWindow]);

  // Dynamically size news scroll area to fill remaining viewport height
  useEffect(() => {
    const measure = () => {
      const el = newsContainerRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      const viewportH = window.innerHeight;
      const bottomPad = 32;
      setNewsMaxHeight(`${Math.max(200, viewportH - top - bottomPad)}px`);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [initialLoading]);

  const handleWindowChange = (w: TimeWindow) => {
    setActiveWindow(w);
    const params = new URLSearchParams();
    if (w !== "24h") params.set("window", w);
    const qs = params.toString();
    router.replace(qs ? `?${qs}` : `/ticker/${symbol}`, { scroll: false });
    fetchData(w);
  };

  const chartData = buildChartData(articles, activeWindow);

  // Fine-grained sorted [timestamp_ms, articleId] pairs — passed to chart for precise hover mapping.
  const articleTsMap: [number, number][] = articles
    .map((a) => [new Date(a.timestamp).getTime(), a.id] as [number, number])
    .sort((a, b) => a[0] - b[0]);

  const highlightedArticleUrl = highlightedId != null
    ? (articles.find((a) => a.id === highlightedId)?.url ?? null)
    : null;

  const [newsHoverTs, setNewsHoverTs] = useState<number | null>(null);

  const handleChartClick = useCallback((articleId: number) => {
    setHighlightedId(articleId);
  }, []);

  const handleChartHover = useCallback((articleId: number | null) => {
    setHighlightedId(articleId);
  }, []);

  const handleNewsHover = useCallback((id: number | null) => {
    if (id === null) {
      setNewsHoverTs(null);
      return;
    }
    const article = articles.find((a) => a.id === id);
    if (article) setNewsHoverTs(new Date(article.timestamp).getTime());
  }, [articles]);

  const windowLabel = WINDOW_LABELS[activeWindow];

  // Displayed identity: prefer staticIdentity (stable), fall back to detail for first load.
  const displaySymbol = staticIdentity?.symbol ?? detail?.symbol ?? symbol.toUpperCase();
  const displayCompany = staticIdentity?.company_name ?? detail?.company_name ?? null;

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
      {/* Back */}
      <motion.div initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}>
        <Link
          href={`/?window=${activeWindow}`}
          className="inline-flex items-center gap-2 text-sm text-[#6B7280] hover:text-[#1A1A1A] transition-colors mb-6 cursor-pointer"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to dashboard
        </Link>
      </motion.div>

      {/* Compact header — symbol and company name are static, only badge/mentions animate */}
      {initialLoading ? (
        <div className="flex items-center gap-4 mb-6">
          <div className="shimmer h-9 w-24 rounded" />
          <div className="shimmer h-5 w-48 rounded" />
        </div>
      ) : (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-wrap items-center gap-3 mb-6"
        >
          {/* Static: symbol + company name — never remount */}
          <div className="flex items-center gap-3">
            <h1
              className="text-3xl font-bold text-[#1A1A1A]"
              style={{ fontFamily: "var(--font-playfair)" }}
            >
              {displaySymbol}
            </h1>
          </div>
          {displayCompany && (
            <span className="text-[#6B7280] text-base">{displayCompany}</span>
          )}

          {/* Dynamic: sentiment badge + mentions — crossfade on window change */}
          <AnimatePresence mode="wait">
            <motion.div
              key={`${activeWindow}-${transitioning ? "loading" : "loaded"}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="flex items-center gap-3"
            >
              {transitioning || !detail ? (
                <>
                  <div className="shimmer h-6 w-16 rounded-full" />
                  <div className="shimmer h-5 w-28 rounded" />
                </>
              ) : (
                <>
                  <SentimentBadge sentiment={detail.sentiment} />
                  <div className="flex items-center gap-1.5 text-[#6B7280] ml-auto">
                    <Newspaper className="w-4 h-4" />
                    <span className="text-base font-semibold">{detail.mentions_24h}</span>
                    <span className="text-base font-semibold">mentions</span>
                    <span className="flex items-center gap-0.5 text-xs text-[#9CA3AF]">
                      {windowLabel}<Clock className="w-3 h-3" />
                    </span>
                  </div>
                </>
              )}
            </motion.div>
          </AnimatePresence>
        </motion.div>
      )}

      {/* Time filter row */}
      <motion.div
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.04 }}
        className="flex items-center justify-end mb-5"
      >
        <TimeFilter value={activeWindow} onChange={handleWindowChange} />
      </motion.div>

      {/* Chart + News side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Sentiment chart */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.08 }}
          className="lg:col-span-2 rounded-xl bg-white border border-[#E5E7EB] p-5"
        >
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-[#374151]">Sentiment Over Time</h2>
            {chartData.length > 0 && !transitioning && (
              <span className="text-xs text-[#9CA3AF]">click a point → highlight article</span>
            )}
          </div>
          <p className="text-xs text-[#D1D5DB] mb-3">Last {windowLabel} buckets</p>
          {chartData.length === 0 && !initialLoading && !transitioning ? (
            <div className="h-48 flex items-center justify-center text-[#9CA3AF] text-sm text-center px-4">
              No available sentiment data
            </div>
          ) : (
            <SentimentChart
              data={chartData}
              articleTsMap={articleTsMap}
              onPointClick={handleChartClick}
              onHoverArticle={handleChartHover}
              externalHoverTs={newsHoverTs}
              highlightedArticleUrl={highlightedArticleUrl}
              transitioning={transitioning || initialLoading}
            />
          )}
        </motion.div>

        {/* News timeline */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.12 }}
          className="lg:col-span-3 rounded-xl bg-white border border-[#E5E7EB] p-5"
          style={{ overflowX: "hidden" }}
        >
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-[#374151]">News Timeline</h2>
            <AnimatePresence mode="wait">
              <motion.span
                key={transitioning ? "loading-count" : `count-${articles.length}`}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="text-xs text-[#9CA3AF]"
              >
                {transitioning ? "…" : `${articles.length} articles`}
              </motion.span>
            </AnimatePresence>
          </div>
          <div
            ref={(el) => {
              (newsScrollRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
              (newsContainerRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
            }}
            className="overflow-y-auto pr-1"
            style={{ maxHeight: newsMaxHeight, overflowX: "hidden" }}
          >
            <NewsTimeline
              articles={articles}
              loading={newsLoading}
              transitioning={transitioning}
              highlightedId={highlightedId}
              scrollContainerRef={newsScrollRef}
              onArticleHover={handleNewsHover}
              emptyMessage={`No current events for ${symbol}`}
            />
          </div>
        </motion.div>
      </div>
    </div>
  );
}

export default function TickerPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  return (
    <Suspense fallback={<div className="max-w-5xl mx-auto px-4 sm:px-6 py-8" />}>
      <TickerContent params={params} />
    </Suspense>
  );
}
