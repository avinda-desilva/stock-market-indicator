"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Search, TrendingUp } from "lucide-react";
import { SearchPalette } from "./SearchPalette";

export function Navbar() {
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <header className="sticky top-0 z-30 bg-[#F8F9FA]/95 backdrop-blur-sm border-b border-[#E5E7EB]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5 hover:opacity-70 transition-opacity cursor-pointer">
            <div className="w-7 h-7 rounded-md bg-[#16A34A] flex items-center justify-center">
              <TrendingUp className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-[#1A1A1A] hidden sm:block tracking-tight" style={{ fontFamily: "var(--font-playfair)" }}>SMI</span>
            <span className="text-[#6B7280] hidden sm:block text-sm">Stock Market Indicator</span>
          </Link>

          <button
            onClick={() => setSearchOpen(true)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white border border-[#E5E7EB] text-[#6B7280] hover:text-[#1A1A1A] hover:border-[#D1D5DB] transition-all duration-200 cursor-pointer text-sm"
          >
            <Search className="w-4 h-4" />
            <span className="hidden sm:block">Search…</span>
            <kbd className="hidden sm:flex items-center gap-0.5 text-xs text-[#9CA3AF] font-mono ml-2">
              <span>⌘</span><span>K</span>
            </kbd>
          </button>
        </div>
      </header>

      <SearchPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
    </>
  );
}
