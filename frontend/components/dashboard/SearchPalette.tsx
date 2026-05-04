"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Search, X, TrendingUp, Newspaper, Hash, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import toast from "react-hot-toast";
import { searchTickers } from "@/lib/api";
import type { SearchResult } from "@/lib/types";
import { SentimentBadge } from "@/components/ui/SentimentBadge";

interface Props {
  open: boolean;
  onClose: () => void;
}

const intentIcon: Record<string, React.ReactNode> = {
  ticker_lookup: <Hash className="w-4 h-4" />,
  sector_search: <TrendingUp className="w-4 h-4" />,
  news_search: <Newspaper className="w-4 h-4" />,
  general_search: <Search className="w-4 h-4" />,
};

export function SearchPalette({ open, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SearchResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleClose = useCallback(() => {
    setQuery("");
    setResult(null);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(id);
  }, [open]);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) { setResult(null); return; }
    setLoading(true);
    try {
      const data = await searchTickers(q);
      setResult(data);
    } catch {
      toast.error("Search failed. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(query), 400);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, doSearch]);

  const goToTicker = (symbol: string) => {
    router.push(`/ticker/${symbol}`);
    handleClose();
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-[#1A1A1A]/30 backdrop-blur-sm z-40"
            onClick={handleClose}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -8 }}
            transition={{ duration: 0.15 }}
            className="fixed top-[15vh] left-1/2 -translate-x-1/2 w-full max-w-2xl z-50 px-4"
          >
            <div className="rounded-xl bg-white border border-[#E5E7EB] shadow-xl overflow-hidden">
              {/* Input */}
              <div className="flex items-center gap-3 px-4 py-3 border-b border-[#F3F4F6]">
                <Search className="w-5 h-5 text-[#9CA3AF] flex-none" />
                <input
                  ref={inputRef}
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") handleClose();
                    if (e.key === "Enter" && result?.tickers[0]) {
                      goToTicker(result.tickers[0].symbol);
                    }
                  }}
                  placeholder='Search tickers, sectors, or news… e.g. "AAPL", "AI stocks"'
                  className="flex-1 bg-transparent text-[#1A1A1A] placeholder-[#9CA3AF] text-base outline-none"
                />
                {loading ? (
                  <Loader2 className="w-4 h-4 text-[#9CA3AF] animate-spin flex-none" />
                ) : query ? (
                  <button
                    onClick={() => setQuery("")}
                    className="text-[#9CA3AF] hover:text-[#6B7280] transition-colors cursor-pointer"
                  >
                    <X className="w-4 h-4" />
                  </button>
                ) : (
                  <kbd className="hidden sm:flex items-center gap-1 px-1.5 py-0.5 rounded text-xs text-[#9CA3AF] border border-[#E5E7EB] font-mono bg-[#F9FAFB]">
                    ESC
                  </kbd>
                )}
              </div>

              {/* Results */}
              {result && (
                <div className="max-h-[60vh] overflow-y-auto divide-y divide-[#F3F4F6]">
                  {/* Intent badge */}
                  <div className="px-4 py-2 flex items-center gap-2">
                    <span className="text-[#9CA3AF] flex-none">
                      {intentIcon[result.intent] ?? <Search className="w-4 h-4" />}
                    </span>
                    <span className="text-xs text-[#9CA3AF] capitalize">
                      {result.intent.replace(/_/g, " ")}
                    </span>
                  </div>

                  {/* Ticker matches */}
                  {result.tickers.length > 0 && (
                    <div>
                      <div className="px-4 py-2 text-xs font-semibold text-[#9CA3AF] uppercase tracking-wider bg-[#F9FAFB]">
                        Tickers
                      </div>
                      {result.tickers.map((t) => (
                        <button
                          key={t.symbol}
                          onClick={() => goToTicker(t.symbol)}
                          className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-[#F9FAFB] transition-colors cursor-pointer text-left"
                        >
                          <div className="flex items-center gap-3">
                            <span
                              className="text-sm font-bold text-[#1A1A1A] w-12"
                              style={{ fontFamily: "var(--font-playfair)" }}
                            >
                              {t.symbol}
                            </span>
                            <div className="flex-1 w-32 h-1 bg-[#F3F4F6] rounded-full overflow-hidden">
                              <div
                                className="h-full rounded-full bg-[#16A34A]"
                                style={{ width: `${Math.min(100, (t.score / 150) * 100)}%` }}
                              />
                            </div>
                          </div>
                          <SentimentBadge sentiment={t.sentiment} size="sm" />
                        </button>
                      ))}
                    </div>
                  )}

                  {/* News matches */}
                  {result.news.length > 0 && (
                    <div>
                      <div className="px-4 py-2 text-xs font-semibold text-[#9CA3AF] uppercase tracking-wider bg-[#F9FAFB]">
                        News
                      </div>
                      {result.news.slice(0, 5).map((article) => (
                        <a
                          key={article.id}
                          href={article.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={handleClose}
                          className="block px-4 py-2.5 hover:bg-[#F9FAFB] transition-colors cursor-pointer"
                        >
                          <p className="text-sm text-[#1A1A1A] line-clamp-1">{article.title}</p>
                          <p className="text-xs text-[#9CA3AF] mt-0.5">
                            {article.source} · {new Date(article.timestamp).toLocaleDateString()}
                          </p>
                        </a>
                      ))}
                    </div>
                  )}

                  {result.tickers.length === 0 && result.news.length === 0 && (
                    <div className="px-4 py-8 text-center text-[#9CA3AF] text-sm">
                      No results found for &ldquo;{result.query}&rdquo;
                    </div>
                  )}
                </div>
              )}

              {/* Footer hint */}
              {!result && !loading && (
                <div className="px-4 py-3 flex items-center gap-4 text-xs text-[#9CA3AF] bg-[#F9FAFB]">
                  <span><kbd className="font-mono">↵</kbd> open ticker</span>
                  <span><kbd className="font-mono">ESC</kbd> close</span>
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
