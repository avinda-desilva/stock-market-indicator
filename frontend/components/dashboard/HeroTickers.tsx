"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import type { TrendingTicker } from "@/lib/types";
import { SentimentBadge } from "@/components/ui/SentimentBadge";
import { SkeletonHeroCard } from "@/components/ui/SkeletonCard";
import type { TimeWindow } from "@/components/ui/TimeFilter";

interface Props {
  tickers: TrendingTicker[];
  loading: boolean;
  window?: TimeWindow;
}

const medals = [
  {
    label: "Gold",
    border: "border-[#D4AF37]",
    accent: "#D4AF37",
    rankBg: "bg-[#D4AF37]/10",
    rankText: "text-[#B8860B]",
  },
  {
    label: "Silver",
    border: "border-[#9CA3AF]",
    accent: "#9CA3AF",
    rankBg: "bg-[#9CA3AF]/10",
    rankText: "text-[#6B7280]",
  },
  {
    label: "Bronze",
    border: "border-[#CD7F32]",
    accent: "#CD7F32",
    rankBg: "bg-[#CD7F32]/10",
    rankText: "text-[#A0522D]",
  },
];

export function HeroTickers({ tickers, loading, window = "24h" }: Props) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[0, 1, 2].map((i) => <SkeletonHeroCard key={i} />)}
      </div>
    );
  }

  const top3 = tickers.slice(0, 3);
  const maxScore = Math.max(...top3.map((t) => t.score), 1);

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {top3.map((ticker, i) => {
        const medal = medals[i];
        return (
          <motion.div
            key={ticker.symbol}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.08, duration: 0.35 }}
          >
            <Link href={`/ticker/${ticker.symbol}?window=${window}`} className="block group cursor-pointer">
              <div className={`rounded-xl bg-white border ${medal.border} p-6 transition-all duration-200 hover:shadow-sm`}>
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full ${medal.rankBg} text-xs font-bold ${medal.rankText}`}>
                      {i + 1}
                    </span>
                    <span className="text-xs font-medium text-[#6B7280] uppercase tracking-widest">{medal.label}</span>
                  </div>
                  <SentimentBadge sentiment={ticker.sentiment} size="sm" />
                </div>

                <h3
                  className="text-3xl font-bold text-[#1A1A1A] tracking-tight mb-1"
                  style={{ fontFamily: "var(--font-playfair)" }}
                >
                  {ticker.symbol}
                </h3>

                {ticker.company_name && (
                  <p className="text-sm text-[#6B7280] mb-4 truncate">{ticker.company_name}</p>
                )}

                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-[#9CA3AF]">{ticker.mentions_24h} mentions ({window})</span>
                  <span className="text-sm font-semibold tabular-nums" style={{ color: medal.accent }}>
                    {ticker.score.toFixed(1)}
                  </span>
                </div>

                <div className="h-0.5 rounded-full bg-[#F3F4F6] overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${Math.min(100, (ticker.score / maxScore) * 100)}%`,
                      backgroundColor: medal.accent,
                    }}
                  />
                </div>
              </div>
            </Link>
          </motion.div>
        );
      })}
    </div>
  );
}
