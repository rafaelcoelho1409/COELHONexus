"use client";

import { useEffect, useState } from "react";

/** The headings above this much of the viewport's bottom count as "in view" (the top quarter band). */
const ACTIVE_BAND = "0px 0px -75% 0px";

/**
 * The id of the last element, among `ids`, that scrolled into the top band of the viewport — the section the
 * reader is in. `ids` must be referentially stable. Does nothing where `IntersectionObserver` does not exist.
 */
export function useScrollSpy(ids: readonly string[]): string | null {
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    if (ids.length === 0 || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) if (entry.isIntersecting) setActive(entry.target.id);
      },
      { rootMargin: ACTIVE_BAND, threshold: 0 },
    );
    for (const id of ids) {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [ids]);

  return active;
}
