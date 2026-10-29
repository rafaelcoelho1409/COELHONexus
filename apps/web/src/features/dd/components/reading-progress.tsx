"use client";

import { useScrollProgress } from "@/shared/lib/use-scroll-progress";

/** A thin bar that fills as the text is read: under the app header for the page, or along the top of the reading window
 * (`scroller` is the box its text scrolls in, `className` where the bar sits). Decorative: the scroll position is already
 * available to everyone, so it is hidden from assistive technology. */
export function ReadingProgress({
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
