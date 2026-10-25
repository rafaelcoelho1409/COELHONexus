"use client";

import { useSyncExternalStore } from "react";

const TICK_MS = 1_000;

// One shared timer for every subscriber; it only runs while somebody is subscribed.
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
let snapshot = 0; // epoch ms of the latest tick; 0 = no tick yet

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    snapshot = Date.now();
    timer = setInterval(() => {
      snapshot = Date.now();
      for (const notify of listeners) notify();
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      clearInterval(timer);
      timer = undefined;
      snapshot = 0;
    }
  };
}

const subscribeIdle = () => () => {};
const getSnapshot = () => snapshot;
const getServerSnapshot = () => 0;

/** Epoch milliseconds, refreshed every second while `active`; `null` when idle or before the first tick. */
export function useClock(active: boolean): number | null {
  const now = useSyncExternalStore(active ? subscribe : subscribeIdle, getSnapshot, getServerSnapshot);
  return active && now > 0 ? now : null;
}
