"use client";

interface Props {
  sentiment: number | null | undefined;
  size?: "sm" | "md";
}

function SentimentAvatar({ sentiment, size }: { sentiment: number | null | undefined; size: "sm" | "md" }) {
  const isPositive = (sentiment ?? 0) > 0.05;
  const isNegative = (sentiment ?? 0) < -0.05;
  const dim = size === "sm" ? "w-4 h-4" : "w-5 h-5";
  const bg = isPositive ? "#16A34A" : isNegative ? "#B91C1C" : "#9CA3AF";

  return (
    <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" className={`${dim} flex-none`}>
      <circle cx="10" cy="10" r="9" fill={bg} fillOpacity="0.12" stroke={bg} strokeWidth="1.5" />
      <circle cx="7.5" cy="8.5" r="1" fill={bg} />
      <circle cx="12.5" cy="8.5" r="1" fill={bg} />
      {isPositive && (
        <path d="M7 12 Q10 15 13 12" stroke={bg} strokeWidth="1.5" strokeLinecap="round" fill="none" />
      )}
      {isNegative && (
        <path d="M7 14 Q10 11 13 14" stroke={bg} strokeWidth="1.5" strokeLinecap="round" fill="none" />
      )}
      {!isPositive && !isNegative && (
        <path d="M7 13 L13 13" stroke={bg} strokeWidth="1.5" strokeLinecap="round" />
      )}
    </svg>
  );
}

export function SentimentBadge({ sentiment, size = "md" }: Props) {
  const val = sentiment ?? 0;
  const isPositive = val > 0.05;
  const isNegative = val < -0.05;

  const colorClass = isPositive
    ? "text-[#15803D] bg-[#F0FDF4] border-[#BBF7D0]"
    : isNegative
    ? "text-[#B91C1C] bg-[#FEF2F2] border-[#FECACA]"
    : "text-[#6B7280] bg-[#F9FAFB] border-[#E5E7EB]";

  const sizeClass = size === "sm" ? "text-xs px-1.5 py-0.5 gap-1.5" : "text-sm px-2 py-1 gap-2";

  return (
    <span className={`inline-flex items-center rounded-full border font-medium ${colorClass} ${sizeClass}`}>
      <SentimentAvatar sentiment={val} size={size} />
      {val !== null && val !== undefined ? `${(val * 100).toFixed(1)}%` : "N/A"}
    </span>
  );
}
