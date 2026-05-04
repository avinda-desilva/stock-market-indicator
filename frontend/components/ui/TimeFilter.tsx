"use client";

import { motion } from "framer-motion";

export type TimeWindow = "6h" | "24h" | "3d" | "7d";

export const TIME_WINDOWS: { value: TimeWindow; label: string }[] = [
  { value: "6h", label: "6h" },
  { value: "24h", label: "24h" },
  { value: "3d", label: "3d" },
  { value: "7d", label: "7d" },
];

interface Props {
  value: TimeWindow;
  onChange: (w: TimeWindow) => void;
}

export function TimeFilter({ value, onChange }: Props) {
  return (
    <div className="flex items-center gap-1 p-1 rounded-lg bg-[#F3F4F6] border border-[#E5E7EB]">
      {TIME_WINDOWS.map((w) => {
        const active = value === w.value;
        return (
          <motion.button
            key={w.value}
            onClick={() => onChange(w.value)}
            whileTap={{ scale: 0.96 }}
            className={`relative px-3 py-1 rounded-md text-sm font-medium transition-colors duration-150 cursor-pointer ${
              active
                ? "text-[#1A1A1A]"
                : "text-[#6B7280] hover:text-[#1A1A1A]"
            }`}
          >
            {active && (
              <motion.span
                layoutId="time-filter-pill"
                className="absolute inset-0 rounded-md bg-white shadow-sm border border-[#E5E7EB]"
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
              />
            )}
            <span className="relative z-10">{w.label}</span>
          </motion.button>
        );
      })}
    </div>
  );
}
