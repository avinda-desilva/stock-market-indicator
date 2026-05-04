"use client";

interface ScoreBarProps {
  value: number;
  max?: number;
  color?: string;
}

export function ScoreBar({ value, max = 100, color }: ScoreBarProps) {
  const pct = Math.min(100, (value / max) * 100);
  const barColor = color ?? "#16A34A";

  return (
    <div className="flex-1 h-1 bg-[#F3F4F6] rounded-full overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, backgroundColor: barColor }}
      />
    </div>
  );
}

// SpikeBadge removed per design decision
export function SpikeBadge({ spike }: { spike: boolean }) {
  void spike;
  return null;
}
