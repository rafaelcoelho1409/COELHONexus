"use client";

import { useScrollProgress } from "@/shared/lib/use-scroll-progress";

/** A thin bar under the app header that fills as the page is read. Decorative: the scroll position is already
 * available to everyone, so it is hidden from assistive technology. */
export function ReadingProgress() {
  const progress = useScrollProgress();
  return (
    <div aria-hidden className="pointer-events-none fixed inset-x-0 top-14 z-40 h-0.5">
      <div className="h-full origin-left bg-primary" style={{ transform: `scaleX(${progress})` }} />
    </div>
  );
}
