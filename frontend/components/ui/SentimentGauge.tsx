"use client";

/**
 * Semicircle sentiment gauge driven exclusively by gauge_sentiment_15m.
 * Returns null when the value is null (no data in last 15 minutes).
 * Intentionally ignores the page ?window= parameter.
 */

interface Props {
  value: number; // [-1.0, 1.0]
}

const R = 52;          // arc radius
const CX = 70;         // center x of the full SVG
const CY = 70;         // center y (bottom of semicircle)
const STROKE = 10;
const NEEDLE_LENGTH = 44;

// Semicircle arc from 180° to 0° (left to right), sitting at the top half.
// We use a fixed arc path so it's a true half-circle.
const ARC_PATH = `M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`;

function polarToXY(angleDeg: number, r: number): { x: number; y: number } {
  const rad = (angleDeg * Math.PI) / 180;
  return {
    x: CX + r * Math.cos(rad),
    y: CY - r * Math.sin(rad),
  };
}

// Maps sentiment [-1, 1] → angle [0°, 180°] (left bearish, right bullish).
function sentimentToAngle(sentiment: number): number {
  const clamped = Math.max(-1, Math.min(1, sentiment));
  return ((clamped + 1) / 2) * 180;
}

export function SentimentGauge({ value }: Props) {
  const angle = sentimentToAngle(value);
  const needle = polarToXY(angle, NEEDLE_LENGTH);

  // Colour: red at -1 → grey at 0 → green at +1
  const needleColor =
    value > 0.1 ? "#16A34A" : value < -0.1 ? "#B91C1C" : "#6B7280";

  const label =
    value >= 0.5
      ? "Bullish"
      : value <= -0.5
      ? "Bearish"
      : value > 0.1
      ? "Slightly Bullish"
      : value < -0.1
      ? "Slightly Bearish"
      : "Neutral";

  return (
    <div className="flex flex-col items-center gap-1">
      <svg
        width={140}
        height={80}
        viewBox={`0 0 140 80`}
        aria-label={`15-minute sentiment gauge: ${label}`}
      >
        {/* Background track — neutral grey */}
        <path
          d={ARC_PATH}
          fill="none"
          stroke="#E5E7EB"
          strokeWidth={STROKE}
          strokeLinecap="round"
        />
        {/* Bearish zone: left quarter (180°–90°) */}
        <path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX} ${CY - R}`}
          fill="none"
          stroke="#FEE2E2"
          strokeWidth={STROKE}
          strokeLinecap="butt"
        />
        {/* Bullish zone: right quarter (90°–0°) */}
        <path
          d={`M ${CX} ${CY - R} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none"
          stroke="#DCFCE7"
          strokeWidth={STROKE}
          strokeLinecap="butt"
        />
        {/* Needle */}
        <line
          x1={CX}
          y1={CY}
          x2={needle.x}
          y2={needle.y}
          stroke={needleColor}
          strokeWidth={2.5}
          strokeLinecap="round"
        />
        {/* Pivot dot */}
        <circle cx={CX} cy={CY} r={4} fill={needleColor} />
        {/* Labels */}
        <text x={CX - R - 4} y={CY + 14} textAnchor="end" fontSize={9} fill="#B91C1C" fontFamily="DM Sans, sans-serif">
          Bear
        </text>
        <text x={CX + R + 4} y={CY + 14} textAnchor="start" fontSize={9} fill="#16A34A" fontFamily="DM Sans, sans-serif">
          Bull
        </text>
      </svg>
      <div className="flex flex-col items-center gap-0.5">
        <span
          className="text-xs font-semibold"
          style={{ color: needleColor }}
        >
          {label}
        </span>
        <span className="text-[10px] text-[#9CA3AF]">15-min sentiment</span>
      </div>
    </div>
  );
}
