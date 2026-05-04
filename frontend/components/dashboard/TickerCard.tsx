"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ChevronRight } from "lucide-react";
import type { TrendingTicker } from "@/lib/types";
import { SentimentBadge } from "@/components/ui/SentimentBadge";
import { ScoreBar } from "@/components/ui/Sparkline";
import type { TimeWindow } from "@/components/ui/TimeFilter";

interface Props {
  ticker: TrendingTicker;
  rank: number;
  index: number;
  maxScore: number;
  window?: TimeWindow;
}

const medalColor: Record<number, { bar: string; rankText: string }> = {
  1: { bar: "#D4AF37", rankText: "text-[#B8860B]" },
  2: { bar: "#9CA3AF", rankText: "text-[#6B7280]" },
  3: { bar: "#CD7F32", rankText: "text-[#A0522D]" },
};
const defaultStyle = { bar: "#16A34A", rankText: "text-[#9CA3AF]" };

export function TickerCard({ ticker, rank, index, maxScore, window = "24h" }: Props) {
  const style = medalColor[rank] ?? defaultStyle;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04, duration: 0.3 }}
    >
      <Link href={`/ticker/${ticker.symbol}?window=${window}`} className="block group cursor-pointer">
        <div className="rounded-xl bg-white border border-[#E5E7EB] p-4 hover:border-[#D1D5DB] hover:shadow-sm transition-all duration-200">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <span className={`flex-none text-xs font-mono w-5 text-right font-semibold ${style.rankText}`}>
                {rank}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span
                    className="text-base font-bold text-[#1A1A1A]"
                    style={{ fontFamily: "var(--font-playfair)" }}
                  >
                    {ticker.symbol}
                  </span>
                </div>
                {ticker.company_name && (
                  <p className="text-xs text-[#6B7280] truncate">{ticker.company_name}</p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-none">
              <SentimentBadge sentiment={ticker.sentiment} size="sm" />
              <ChevronRight className="w-4 h-4 text-[#D1D5DB] group-hover:text-[#9CA3AF] transition-colors" />
            </div>
          </div>

          <div className="mt-3 pl-8">
            <ScoreBar value={ticker.score} max={maxScore} color={style.bar} />
          </div>
        </div>
      </Link>
    </motion.div>
  );
}
