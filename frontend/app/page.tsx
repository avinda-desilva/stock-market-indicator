"use client";

import { useState, useEffect, useCallback, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import toast from "react-hot-toast";
import { RefreshCw } from "lucide-react";
import { motion } from "framer-motion";
import { getTrending, getTrendingSectors } from "@/lib/api";
import type { TrendingTicker } from "@/lib/types";
import { HeroTickers } from "@/components/dashboard/HeroTickers";
import { SectorNav } from "@/components/dashboard/SectorNav";
import { TickerCard } from "@/components/dashboard/TickerCard";
import { SkeletonCard } from "@/components/ui/SkeletonCard";
import { TimeFilter, type TimeWindow } from "@/components/ui/TimeFilter";

function HomeContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const initialWindow = (searchParams.get("window") as TimeWindow) || "24h";
  const initialSector = searchParams.get("sector") || null;

  const [tickers, setTickers] = useState<TrendingTicker[]>([]);
  const [sectors, setSectors] = useState<string[]>([]);
  const [activeSector, setActiveSector] = useState<string | null>(initialSector);
  const [activeWindow, setActiveWindow] = useState<TimeWindow>(initialWindow);
  const [loading, setLoading] = useState(true);
  const [sectorsLoading, setSectorsLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const updateUrl = useCallback((w: TimeWindow, sector: string | null) => {
    const params = new URLSearchParams();
    if (w !== "24h") params.set("window", w);
    if (sector) params.set("sector", sector);
    const qs = params.toString();
    router.replace(qs ? `?${qs}` : "/", { scroll: false });
  }, [router]);

  const fetchTickers = useCallback(async (sector: string | null, w: TimeWindow, silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const data = await getTrending(sector ?? undefined, w);
      setTickers(data);
    } catch {
      toast.error("Failed to load trending tickers");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const fetchSectors = useCallback(async () => {
    setSectorsLoading(true);
    try {
      const data = await getTrendingSectors();
      setSectors(data);
    } catch {
      // Sectors are optional
    } finally {
      setSectorsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTickers(initialSector, initialWindow);
    fetchSectors();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSectorChange = (sector: string | null) => {
    setActiveSector(sector);
    updateUrl(activeWindow, sector);
    fetchTickers(sector, activeWindow);
  };

  const handleWindowChange = (w: TimeWindow) => {
    setActiveWindow(w);
    updateUrl(w, activeSector);
    fetchTickers(activeSector, w);
  };

  const handleRefresh = () => fetchTickers(activeSector, activeWindow, true);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-8">
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1
              className="text-3xl sm:text-4xl font-bold text-[#1A1A1A] tracking-tight"
              style={{ fontFamily: "var(--font-playfair)" }}
            >
              Trending Tickers
            </h1>
            <p className="text-[#6B7280] mt-1 text-sm sm:text-base">
              Real-time market sentiment scored by mentions &amp; social signals
            </p>
          </div>
          <div className="flex items-center gap-3">
            <TimeFilter value={activeWindow} onChange={handleWindowChange} />
            <button
              onClick={handleRefresh}
              disabled={refreshing || loading}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white border border-[#E5E7EB] text-[#6B7280] hover:text-[#1A1A1A] hover:border-[#D1D5DB] transition-all duration-200 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed text-sm"
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} />
              <span className="hidden sm:block">Refresh</span>
            </button>
          </div>
        </div>
      </motion.div>

      <section className="mb-8">
        <HeroTickers tickers={tickers} loading={loading} window={activeWindow} />
      </section>

      <section className="mb-6">
        <SectorNav
          sectors={sectors}
          active={activeSector}
          onChange={handleSectorChange}
          loading={sectorsLoading}
        />
      </section>

      <section>
        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Array.from({ length: 9 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : tickers.length === 0 ? (
          <div className="text-center py-20 text-[#9CA3AF]">
            <p className="text-lg mb-2">No trending tickers yet</p>
            <p className="text-sm">Data appears once ingestion workers run.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {(() => {
              const maxScore = Math.max(...tickers.map((t) => t.score), 1);
              return tickers.map((ticker, i) => (
                <TickerCard
                  key={ticker.symbol}
                  ticker={ticker}
                  rank={i + 1}
                  index={i}
                  maxScore={maxScore}
                  window={activeWindow}
                />
              ));
            })()}
          </div>
        )}
      </section>
    </div>
  );
}

export default function HomePage() {
  return (
    <Suspense fallback={<div className="max-w-7xl mx-auto px-4 sm:px-6 py-8" />}>
      <HomeContent />
    </Suspense>
  );
}
