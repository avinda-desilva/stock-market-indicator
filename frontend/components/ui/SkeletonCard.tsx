"use client";

export function SkeletonCard({ className = "" }: { className?: string }) {
  return (
    <div className={`rounded-xl bg-white border border-[#E5E7EB] p-4 ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="shimmer h-5 w-16 rounded" />
        <div className="shimmer h-4 w-10 rounded" />
      </div>
      <div className="shimmer h-4 w-32 rounded mb-2" />
      <div className="shimmer h-4 w-24 rounded" />
    </div>
  );
}

export function SkeletonHeroCard() {
  return (
    <div className="rounded-xl bg-white border border-[#E5E7EB] p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="shimmer h-8 w-20 rounded" />
        <div className="shimmer h-6 w-16 rounded-full" />
      </div>
      <div className="shimmer h-5 w-40 rounded mb-3" />
      <div className="shimmer h-4 w-28 rounded mb-4" />
      <div className="flex gap-4">
        <div className="shimmer h-10 w-20 rounded" />
        <div className="shimmer h-10 w-20 rounded" />
      </div>
    </div>
  );
}

export function SkeletonNewsItem() {
  return (
    <div className="flex gap-3 py-3">
      <div className="flex flex-col items-center">
        <div className="shimmer w-2 h-2 rounded-full mt-1" />
        <div className="shimmer w-0.5 flex-1 mt-1" />
      </div>
      <div className="flex-1">
        <div className="shimmer h-4 w-3/4 rounded mb-2" />
        <div className="shimmer h-3 w-1/2 rounded" />
      </div>
    </div>
  );
}
