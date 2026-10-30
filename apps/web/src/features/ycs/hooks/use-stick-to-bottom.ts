"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Distance from the page bottom that still counts as "at the bottom". */
const BOTTOM_SLACK_PX = 80;

function gapToBottom(): number {
  return document.documentElement.scrollHeight - (window.scrollY + window.innerHeight);
}

/**
 * Keeps the page pinned to its end while a reply grows, and lets go the moment the reader scrolls up (wheel, touch, keys or
 * scrollbar all move the page up). Scrolling back to the end, or `jump()`, picks the following up again. Pass something that
 * changes whenever the content does (`signal`). Movement is instant, never smoothed, so reduced-motion needs no special case.
 */
export function useStickToBottom(signal: unknown): { following: boolean; jump: () => void } {
  const [following, setFollowing] = useState(true);
  const followingRef = useRef(true);
  const lastY = useRef(0);

  const set = useCallback((next: boolean) => {
    followingRef.current = next;
    setFollowing(next);
  }, []);

  useEffect(() => {
    lastY.current = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      const up = y < lastY.current;
      lastY.current = y;
      if (gapToBottom() < BOTTOM_SLACK_PX) set(true);
      else if (up) set(false);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [set]);

  useEffect(() => {
    void signal; // the trigger: this runs whenever the content changed
    if (!followingRef.current) return;
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
  }, [signal]);

  const jump = useCallback(() => {
    set(true);
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
  }, [set]);

  return { following, jump };
}
