"use client";

import { useScrollProgress } from "@/shared/lib/use-scroll-progress";

export function BookReadingProgress({
  scroller = null,
  className = "fixed inset-x-0 top-14 z-40 h-0.5",
}: {
  scroller?: HTMLElement | null;
  className?: string;
}) {
  const progress = useScrollProgress(scroller);
  return (
    <div aria-hidden className={`pointer-events-none ${className}`}>
      <div className="h-full origin-left bg-primary" style={{ transform: `scaleX(${progress})` }} />
    </div>
  );
}
