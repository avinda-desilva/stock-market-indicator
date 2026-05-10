"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { createPortal } from "react-dom";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  ReferenceLine,
} from "recharts";

export interface ChartPoint {
  time: string;
  ts: number;
  sentiment: number;
  mentions: number;
  articleId: number;
}

interface Props {
  data: ChartPoint[];
  // Sorted [timestamp_ms, articleId] pairs for all articles in the window.
  // Enables fine-grained hover→article mapping and correct domain bounds.
  articleTsMap?: [number, number][];
  onPointClick?: (articleId: number) => void;
  externalHoverTs?: number | null;
  onHoverArticle?: (articleId: number | null) => void;
  highlightedArticleUrl?: string | null;
  transitioning?: boolean;
  // Optional explicit domain max — extends chart x-axis beyond last bucket start
  // so article timestamps near the right edge aren't clamped to it.
  domainMax?: number;
}

function formatMs(ms: number): { time: string; date: string } {
  const d = new Date(ms);
  const hour = d.getHours();
  const min = d.getMinutes();
  const ampm = hour < 12 ? "AM" : "PM";
  const h12 = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const timeStr = min === 0
    ? `${h12}:00 ${ampm}`
    : `${h12}:${String(min).padStart(2, "0")} ${ampm}`;
  return { time: timeStr, date: `${months[d.getMonth()]} ${d.getDate()}` };
}

function formatShort(ms: number): string {
  const { time, date } = formatMs(ms);
  return `${time} · ${date}`;
}

function buildGradientStops(data: ChartPoint[]) {
  const sentiments = data.map((d) => d.sentiment);
  const max = Math.max(...sentiments, 0);
  const min = Math.min(...sentiments, 0);
  const range = max - min;
  const zeroFraction = range === 0 ? 0.5 : max / range;
  return { zeroFraction };
}

function interpolateSentiment(data: ChartPoint[], ts: number): number {
  if (data.length === 0) return 0;
  if (data.length === 1) return data[0].sentiment;
  if (ts <= data[0].ts) return data[0].sentiment;
  if (ts >= data[data.length - 1].ts) return data[data.length - 1].sentiment;
  for (let i = 0; i < data.length - 1; i++) {
    const a = data[i];
    const b = data[i + 1];
    if (ts >= a.ts && ts <= b.ts) {
      const t = (ts - a.ts) / (b.ts - a.ts);
      return a.sentiment + t * (b.sentiment - a.sentiment);
    }
  }
  return data[data.length - 1].sentiment;
}

// Find nearest bucket point to ts (used for chart rendering reference line position).
function closestBucket(data: ChartPoint[], ts: number): ChartPoint {
  let best = data[0];
  let bestDiff = Math.abs(data[0].ts - ts);
  for (const pt of data) {
    const diff = Math.abs(pt.ts - ts);
    if (diff < bestDiff) { bestDiff = diff; best = pt; }
  }
  return best;
}

// Find the article whose timestamp is nearest to ts, returning its id.
// articleMap: sorted array of [timestamp_ms, articleId] pairs.
function closestArticleId(
  articleMap: [number, number][],
  ts: number,
): number {
  if (!articleMap.length) return 0;
  let lo = 0, hi = articleMap.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (articleMap[mid][0] < ts) lo = mid + 1;
    else hi = mid;
  }
  // lo is the first entry >= ts; compare with lo-1
  if (lo > 0 && Math.abs(articleMap[lo - 1][0] - ts) <= Math.abs(articleMap[lo][0] - ts)) {
    lo = lo - 1;
  }
  return articleMap[lo][1];
}

function tsToRatio(ts: number, minTs: number, maxTs: number): number {
  if (maxTs === minTs) return 0;
  return Math.max(0, Math.min(1, (ts - minTs) / (maxTs - minTs)));
}

function SentimentIcon({ sentiment }: { sentiment: number }) {
  if (sentiment > 0.05) return <TrendingUp className="w-3 h-3" />;
  if (sentiment < -0.05) return <TrendingDown className="w-3 h-3" />;
  return <Minus className="w-3 h-3" />;
}

const LEFT_MARGIN = 44;
const RIGHT_MARGIN = 16;
const COLLISION_THRESHOLD = 56;

interface HoverState {
  screenX: number;
  screenTop: number;
  screenBottom: number;
  ts: number;
  ratio: number;
}

// FADE_MS must match the CSS transition duration used on the chart wrapper.
const FADE_MS = 160;

export function SentimentChart({
  data,
  articleTsMap,
  onPointClick,
  externalHoverTs,
  onHoverArticle,
  highlightedArticleUrl,
  transitioning = false,
  domainMax,
}: Props) {
  const [hover, setHover] = useState<HoverState | null>(null);
  const suppressOverlay = useRef(false);
  const chartRef = useRef<HTMLDivElement>(null);

  // displayData is what the chart actually renders. It lags behind `data` by one
  // fade cycle so the old chart fades out before the new data appears.
  const [displayData, setDisplayData] = useState<ChartPoint[]>(data);
  const [displayMap, setDisplayMap] = useState<[number, number][] | undefined>(articleTsMap);
  // chartOpacity drives the CSS transition — 1 normally, 0 while fading out.
  const [chartOpacity, setChartOpacity] = useState(1);
  const fadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevTransitioning = useRef(transitioning);

  useEffect(() => {
    // transitioning just turned true → start fade-out.
    if (transitioning && !prevTransitioning.current) {
      if (fadeTimer.current) clearTimeout(fadeTimer.current);
      setChartOpacity(0);
    }

    // transitioning just turned false → new data is ready. Swap display data,
    // then fade back in. The swap happens at opacity=0 so it's invisible.
    if (!transitioning && prevTransitioning.current) {
      if (fadeTimer.current) clearTimeout(fadeTimer.current);
      // Swap data while still invisible.
      setDisplayData(data);
      setDisplayMap(articleTsMap);
      // Small rAF delay ensures the DOM has re-rendered with new data before
      // the opacity transition starts, preventing any flash.
      fadeTimer.current = setTimeout(() => {
        setChartOpacity(1);
      }, 16);
    }

    prevTransitioning.current = transitioning;
    return () => {
      if (fadeTimer.current) clearTimeout(fadeTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transitioning]);

  // On initial mount (not a transition), keep displayData in sync with data.
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      setDisplayData(data);
      setDisplayMap(articleTsMap);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Chart domain spans from the earliest bucket to the latest article timestamp (or domainMax).
  // This ensures article timestamps near the end of the window aren't clamped to the last bucket start.
  const bucketTimestamps = displayData.map((d) => d.ts);
  const minTs = bucketTimestamps.length ? Math.min(...bucketTimestamps) : 0;
  const bucketMaxTs = bucketTimestamps.length ? Math.max(...bucketTimestamps) : 1;

  // Extend domain right bound to cover actual article timestamps so hover lines place correctly.
  const articleTsList = displayMap && displayMap.length ? displayMap.map(([ts]) => ts) : bucketTimestamps;
  const labelMinTs = articleTsList.length ? Math.min(...articleTsList) : minTs;
  const labelMaxTs = articleTsList.length ? Math.max(...articleTsList) : bucketMaxTs;
  const maxTs = domainMax != null
    ? Math.max(bucketMaxTs, domainMax)
    : Math.max(bucketMaxTs, labelMaxTs);

  // Fallback article map from bucket data when fine-grained map isn't supplied.
  const resolvedMap: [number, number][] = displayMap && displayMap.length
    ? displayMap
    : displayData.map((d) => [d.ts, d.articleId] as [number, number]);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!displayData.length) return;
    const el = chartRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const plotLeft = rect.left + LEFT_MARGIN;
    const plotRight = rect.right - RIGHT_MARGIN;
    const plotWidth = plotRight - plotLeft;
    const ratio = Math.max(0, Math.min(1, (e.clientX - plotLeft) / plotWidth));
    const ts = minTs + ratio * (maxTs - minTs);
    suppressOverlay.current = false;
    setHover({ screenX: plotLeft + ratio * plotWidth, screenTop: rect.top, screenBottom: rect.bottom, ts, ratio });
    onHoverArticle?.(closestArticleId(resolvedMap, ts));
  }, [displayData, minTs, maxTs, onHoverArticle, resolvedMap]);

  const handleMouseLeave = useCallback(() => {
    setHover(null);
    onHoverArticle?.(null);
  }, [onHoverArticle]);

  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    (e.currentTarget as HTMLElement).blur();
    if (!displayData.length) return;
    if (highlightedArticleUrl) {
      suppressOverlay.current = true;
      setHover(null);
      window.open(highlightedArticleUrl, "_blank", "noopener,noreferrer");
      return;
    }
    if (!onPointClick || !hover) return;
    onPointClick(closestArticleId(resolvedMap, hover.ts));
  }, [displayData, onPointClick, highlightedArticleUrl, hover, resolvedMap]);

  if (!displayData.length) {
    return (
      <div className="h-48 flex items-center justify-center text-[#9CA3AF] text-sm">
        No chart data available
      </div>
    );
  }

  // Resolve active state: internal hover wins, then external
  const activeTs = hover !== null ? hover.ts : externalHoverTs ?? null;
  const activeSentiment = activeTs !== null ? interpolateSentiment(displayData, activeTs) : null;
  const sentimentColor = activeSentiment !== null
    ? activeSentiment > 0.05 ? "#16A34A" : activeSentiment < -0.05 ? "#B91C1C" : "#9CA3AF"
    : "#9CA3AF";

  // Screen coords for overlays
  let screenX: number | null = null;
  let screenTop: number | null = null;
  let screenBottom: number | null = null;
  let activeRatio: number | null = null;

  if (hover !== null) {
    screenX = hover.screenX;
    screenTop = hover.screenTop;
    screenBottom = hover.screenBottom;
    activeRatio = hover.ratio;
  } else if (externalHoverTs != null && chartRef.current) {
    const rect = chartRef.current.getBoundingClientRect();
    const plotLeft = rect.left + LEFT_MARGIN;
    const plotRight = rect.right - RIGHT_MARGIN;
    const plotWidth = plotRight - plotLeft;
    activeRatio = tsToRatio(externalHoverTs, minTs, maxTs);
    screenX = plotLeft + activeRatio * plotWidth;
    screenTop = rect.top;
    screenBottom = rect.bottom;
  }

  const plotPx = (chartRef.current?.offsetWidth ?? 400) - LEFT_MARGIN - RIGHT_MARGIN;
  const hideStart = activeRatio !== null && activeRatio < COLLISION_THRESHOLD / plotPx;
  const hideEnd = activeRatio !== null && activeRatio > 1 - COLLISION_THRESHOLD / plotPx;

  // If the domain extends past the last bucket (because a recent article timestamp
  // is newer than the last bucket start), pad a synthetic point at maxTs so the
  // line reaches the right edge instead of stopping at the last bucket.
  const lastBucket = displayData[displayData.length - 1];
  const paddedData =
    lastBucket && maxTs > lastBucket.ts
      ? [
          ...displayData,
          { ...lastBucket, ts: maxTs, time: formatShort(maxTs) },
        ]
      : displayData;

  const domain: [number, number] = [minTs, maxTs];
  const { zeroFraction } = buildGradientStops(paddedData);
  const zeroPct = `${(zeroFraction * 100).toFixed(1)}%`;
  const startLabel = formatMs(labelMinTs);
  const endLabel = formatMs(labelMaxTs);

  // Portaled overlays — attached to document.body so they never affect any layout
  const showOverlay = !suppressOverlay.current;
  const overlays = typeof document !== "undefined" ? createPortal(
    <>
      {showOverlay && activeSentiment !== null && screenX !== null && screenTop !== null && (
        <div
          style={{
            position: "fixed",
            left: screenX,
            top: screenTop + 4,
            transform: "translateX(-50%)",
            zIndex: 9999,
            pointerEvents: "none",
            whiteSpace: "nowrap",
          }}
        >
          <div style={{
            background: "#fff",
            border: "1px solid #E5E7EB",
            borderRadius: 8,
            padding: "4px 10px",
            boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
            fontSize: 12,
            display: "flex",
            alignItems: "center",
            gap: 4,
          }}>
            <span style={{ color: sentimentColor, display: "flex" }}>
              <SentimentIcon sentiment={activeSentiment} />
            </span>
            <span style={{ color: sentimentColor, fontWeight: 600 }}>
              {activeSentiment > 0 ? "+" : ""}{(activeSentiment * 100).toFixed(1)}%
            </span>
          </div>
        </div>
      )}
      {showOverlay && screenX !== null && screenBottom !== null && activeTs !== null && (
        <div
          style={{
            position: "fixed",
            left: screenX,
            top: screenBottom + 6,
            transform: "translateX(-50%)",
            zIndex: 9999,
            pointerEvents: "none",
            whiteSpace: "nowrap",
            fontSize: 11,
            color: "#374151",
            fontWeight: 500,
          }}
        >
          {formatShort(activeTs)}
        </div>
      )}
    </>,
    document.body
  ) : null;

  return (
    <div className="sentiment-chart-root">
      {overlays}

      {/* Single wrapper whose opacity we control — uniform fade-out then fade-in on every transition */}
      <div
        style={{
          opacity: chartOpacity,
          transition: `opacity ${FADE_MS}ms ease`,
          pointerEvents: chartOpacity < 1 ? "none" : "auto",
        }}
      >
        <div
          ref={chartRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          onClick={handleClick}
          tabIndex={-1}
          style={{ outline: "none" }}
        >
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={paddedData} margin={{ top: 10, right: RIGHT_MARGIN, left: -16, bottom: 0 }}>
              <defs>
                <linearGradient id="sentStroke" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#16A34A" />
                  <stop offset={zeroPct} stopColor="#16A34A" />
                  <stop offset={zeroPct} stopColor="#B91C1C" />
                  <stop offset="100%" stopColor="#B91C1C" />
                </linearGradient>
                <linearGradient id="sentFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#16A34A" stopOpacity={0.18} />
                  <stop offset={zeroPct} stopColor="#16A34A" stopOpacity={0.04} />
                  <stop offset={zeroPct} stopColor="#B91C1C" stopOpacity={0.04} />
                  <stop offset="100%" stopColor="#B91C1C" stopOpacity={0.18} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#F3F4F6" />
              <XAxis dataKey="ts" type="number" scale="time" domain={domain} hide />
              <YAxis
                tick={{ fill: "#9CA3AF", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
              />
              <ReferenceLine y={0} stroke="#E5E7EB" strokeDasharray="4 4" />
              {activeTs !== null && (
                <ReferenceLine x={activeTs} stroke="#9CA3AF" strokeWidth={1} strokeDasharray="3 3" />
              )}
              <Area
                type="monotone"
                dataKey="sentiment"
                stroke="url(#sentStroke)"
                strokeWidth={1.5}
                fill="url(#sentFill)"
                dot={false}
                connectNulls
                activeDot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Static x-axis label row */}
        <div style={{ position: "relative", height: 36, marginLeft: LEFT_MARGIN, marginRight: RIGHT_MARGIN }}>
          <div style={{
            position: "absolute", left: 0, top: 0,
            transform: "translateX(-50%)", textAlign: "center",
            opacity: hideStart ? 0 : 1, transition: "opacity 0.1s",
            whiteSpace: "nowrap",
          }}>
            <div style={{ fontSize: 11, color: "#6B7280", lineHeight: 1.2 }}>{startLabel.time}</div>
            <div style={{ fontSize: 9, color: "#9CA3AF" }}>{startLabel.date}</div>
          </div>
          <div style={{
            position: "absolute", right: 0, top: 0,
            transform: "translateX(50%)", textAlign: "center",
            opacity: hideEnd ? 0 : 1, transition: "opacity 0.1s",
            whiteSpace: "nowrap",
          }}>
            <div style={{ fontSize: 11, color: "#6B7280", lineHeight: 1.2 }}>{endLabel.time}</div>
            <div style={{ fontSize: 9, color: "#9CA3AF" }}>{endLabel.date}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
