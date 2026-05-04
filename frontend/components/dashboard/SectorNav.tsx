"use client";

import { motion } from "framer-motion";
import { Globe } from "lucide-react";

interface Props {
  sectors: string[];
  active: string | null;
  onChange: (sector: string | null) => void;
  loading: boolean;
}

function formatSectorLabel(key: string) {
  return key.replace(/^trending:/, "").replace(/_/g, " ");
}

export function SectorNav({ sectors, active, onChange, loading }: Props) {
  const allOption = { key: null, label: "All Sectors" };

  return (
    <div className="flex items-center gap-2 overflow-x-auto pb-1 scrollbar-none">
      <button
        onClick={() => onChange(null)}
        className={`flex-none flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-all duration-200 cursor-pointer whitespace-nowrap
          ${active === null
            ? "bg-[#16A34A] text-white"
            : "bg-white border border-[#E5E7EB] text-[#6B7280] hover:border-[#D1D5DB] hover:text-[#1A1A1A]"
          }`}
      >
        <Globe className="w-3.5 h-3.5" />
        {allOption.label}
      </button>

      {loading
        ? Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="shimmer flex-none h-7 w-24 rounded-full" />
          ))
        : sectors.map((sector) => {
            const label = formatSectorLabel(sector);
            const isActive = active === sector;
            return (
              <motion.button
                key={sector}
                onClick={() => onChange(sector)}
                whileTap={{ scale: 0.97 }}
                className={`flex-none px-3 py-1.5 rounded-full text-sm font-medium transition-all duration-200 cursor-pointer whitespace-nowrap
                  ${isActive
                    ? "bg-[#16A34A] text-white"
                    : "bg-white border border-[#E5E7EB] text-[#6B7280] hover:border-[#D1D5DB] hover:text-[#1A1A1A]"
                  }`}
              >
                {label}
              </motion.button>
            );
          })}
    </div>
  );
}
