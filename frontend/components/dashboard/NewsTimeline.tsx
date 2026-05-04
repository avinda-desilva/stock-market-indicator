"use client";

import { useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import type { Article } from "@/lib/types";
import { SkeletonNewsItem } from "@/components/ui/SkeletonCard";
import { AnimatePresence, motion } from "framer-motion";

interface Props {
  articles: Article[];
  loading: boolean;
  transitioning?: boolean;
  highlightedId?: number | null;
  scrollContainerRef?: React.RefObject<HTMLDivElement | null>;
  onArticleHover?: (id: number | null) => void;
  emptyMessage?: string;
}

function sentimentColor(sentiment?: number | null) {
  if (sentiment === undefined || sentiment === null) return "#D1D5DB";
  if (sentiment > 0.05) return "#16A34A";
  if (sentiment < -0.05) return "#B91C1C";
  return "#D1D5DB";
}

function sentimentPct(sentiment?: number | null): string {
  if (sentiment === undefined || sentiment === null) return "–";
  const pct = Math.round(Math.abs(sentiment) * 100);
  return (sentiment >= 0 ? "+" : "−") + pct + "%";
}

function timeAgo(timestamp: string) {
  const diff = Date.now() - new Date(timestamp).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTimestamp(timestamp: string): string {
  const d = new Date(timestamp);
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const hour = d.getHours();
  const min = d.getMinutes();
  const ampm = hour < 12 ? "AM" : "PM";
  const h12 = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
  return `${months[d.getMonth()]} ${d.getDate()}, ${String(h12).padStart(2, "0")}:${String(min).padStart(2, "0")} ${ampm}`;
}

function ArticleRow({
  article,
  isHighlighted,
  onMouseEnter,
  onMouseLeave,
  refCallback,
}: {
  article: Article;
  isHighlighted: boolean;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  refCallback: (el: HTMLDivElement | null) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const dotColor = sentimentColor(article.sentiment);

  const handleEnter = () => {
    setHovered(true);
    onMouseEnter();
  };
  const handleLeave = () => {
    setHovered(false);
    onMouseLeave();
  };

  return (
    <div
      ref={refCallback}
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
      className={`relative flex gap-4 py-3 group rounded-lg transition-all duration-300 ${
        isHighlighted
          ? "bg-[#F0FDF4] ring-1 ring-[#16A34A]/20 px-2 -mx-2"
          : ""
      }`}
    >
      {/* Dot / sentiment reveal */}
      <div className="relative z-10 flex-none mt-1" style={{ width: 14, height: 14, overflow: "visible" }}>
        {/* Default dot — fades out on hover */}
        <div
          className="absolute rounded-full border-2 border-[#F8F9FA]"
          style={{
            inset: 0,
            backgroundColor: dotColor,
            opacity: hovered ? 0 : 1,
            transform: hovered ? "scale(1.1)" : "scale(1)",
            transition: "opacity 150ms ease, transform 200ms ease",
          }}
        />
        {/* Sentiment % chip — fades in on hover, centered on dot, allowed to overflow */}
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: 0,
            transform: "translateY(-50%)",
            opacity: hovered ? 1 : 0,
            transition: "opacity 150ms ease",
            width: 36,
            textAlign: "center",
            fontSize: 10,
            fontWeight: 700,
            color: dotColor === "#D1D5DB" ? "#6B7280" : dotColor,
            lineHeight: 1,
            pointerEvents: "none",
            background: "#fff",
            padding: "1px 3px",
            borderRadius: 3,
            border: `1px solid ${dotColor === "#D1D5DB" ? "#E5E7EB" : dotColor + "44"}`,
          }}
        >
          {sentimentPct(article.sentiment)}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <a
          href={article.url ?? undefined}
          target="_blank"
          rel="noopener noreferrer"
          className="group/link"
          style={{
            display: "block",
            transform: hovered ? "translateX(15px)" : "translateX(0)",
            transition: "transform 150ms ease",
          }}
        >
          <p className={`text-sm leading-snug line-clamp-2 transition-colors ${
            isHighlighted
              ? "text-[#1A1A1A] font-medium"
              : "text-[#374151] group-hover/link:text-[#1A1A1A]"
          }`}>
            {article.title}
          </p>
        </a>
        <div
          className="flex items-center gap-2 mt-1"
          style={{
            transform: hovered ? "translateX(8px)" : "translateX(0)",
            transition: "transform 150ms ease",
          }}
        >
          <span className="text-xs text-[#9CA3AF]">{article.source}</span>
          <span className="text-[#D1D5DB]">·</span>
          {/* Time label — crossfades between relative "Xh ago" and absolute timestamp on hover */}
          <span className="relative text-xs text-[#9CA3AF] tabular-nums" style={{ display: "inline-block", minHeight: "1em" }}>
            <span
              style={{
                display: "inline-block",
                opacity: hovered ? 0 : 1,
                transition: "opacity 120ms ease",
                pointerEvents: hovered ? "none" : "auto",
              }}
            >
              {timeAgo(article.timestamp)}
            </span>
            <span
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                whiteSpace: "nowrap",
                opacity: hovered ? 1 : 0,
                transition: "opacity 120ms ease",
                pointerEvents: hovered ? "auto" : "none",
              }}
            >
              {formatTimestamp(article.timestamp)}
            </span>
          </span>
          {article.url && (
            <a
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity text-[#9CA3AF] hover:text-[#6B7280] cursor-pointer"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

export function NewsTimeline({ articles, loading, transitioning = false, highlightedId, scrollContainerRef, onArticleHover, emptyMessage }: Props) {
  const itemRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    if (highlightedId == null) return;
    const el = itemRefs.current[highlightedId];
    if (!el) return;
    const container = scrollContainerRef?.current;
    if (container) {
      const elTop = el.offsetTop;
      const elHeight = el.offsetHeight;
      const containerHeight = container.clientHeight;
      container.scrollTo({
        top: elTop - containerHeight / 2 + elHeight / 2,
        behavior: "smooth",
      });
    } else {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [highlightedId, scrollContainerRef]);

  // First load: show skeleton directly.
  if (loading && !transitioning) {
    return (
      <div className="divide-y divide-[#F3F4F6]">
        {Array.from({ length: 5 }).map((_, i) => <SkeletonNewsItem key={i} />)}
      </div>
    );
  }

  const content = (
    <>
      {articles.length === 0 ? (
        <p className="text-sm text-[#9CA3AF] py-4">{emptyMessage ?? "No recent news found."}</p>
      ) : (
        <div className="relative">
          <div className="absolute left-[7px] top-3 bottom-3 w-px bg-[#E5E7EB]" />
          <div className="space-y-0">
            {articles.map((article, i) => (
              <ArticleRow
                key={article.id ?? i}
                article={article}
                isHighlighted={article.id === highlightedId}
                onMouseEnter={() => article.id != null && onArticleHover?.(article.id)}
                onMouseLeave={() => onArticleHover?.(null)}
                refCallback={(el) => { if (article.id != null) itemRefs.current[article.id] = el; }}
              />
            ))}
          </div>
        </div>
      )}
    </>
  );

  // Window transition: crossfade between old content and skeleton via AnimatePresence.
  return (
    <div style={{ position: "relative" }}>
      <AnimatePresence mode="wait">
        {transitioning ? (
          <motion.div
            key="skeleton"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="divide-y divide-[#F3F4F6]"
          >
            {Array.from({ length: 5 }).map((_, i) => <SkeletonNewsItem key={i} />)}
          </motion.div>
        ) : (
          <motion.div
            key="content"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            {content}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
