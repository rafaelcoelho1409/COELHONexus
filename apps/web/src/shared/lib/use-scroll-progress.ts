"use client";

import { useEffect, useState } from "react";

/** How far down the page — or down `scroller`, when the text scrolls inside a box of its own — the reader is, 0 at the top
 * to 1 at the bottom; updates at most once per frame. */
export function useScrollProgress(scroller: HTMLElement | null = null): number {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      const scrollable = scroller
        ? scroller.scrollHeight - scroller.clientHeight
        : document.documentElement.scrollHeight - window.innerHeight;
      const scrolled = scroller ? scroller.scrollTop : window.scrollY;
      setProgress(scrollable > 0 ? Math.min(1, Math.max(0, scrolled / scrollable)) : 0);
    };
    const onScroll = () => {
      if (frame === 0) frame = requestAnimationFrame(measure);
    };
    const target: HTMLElement | Window = scroller ?? window;
    target.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    frame = requestAnimationFrame(measure);
    return () => {
      target.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(frame);
    };
  }, [scroller]);

  return progress;
}
