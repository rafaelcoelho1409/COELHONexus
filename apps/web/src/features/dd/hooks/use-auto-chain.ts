"use client";

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";

import { fetchPipelineActive } from "../api";
import { AUTO_CHAIN_MAX_WAIT_MS, AUTO_CHAIN_POLL_MS, AUTO_CHAIN_STORAGE_KEY } from "../params";

// The opt-in lives in localStorage (same key the old UI used); the store keeps every checkbox in sync.
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function readEnabled(): boolean {
  try {
    return window.localStorage.getItem(AUTO_CHAIN_STORAGE_KEY) === "1";
  } catch {
    return false; // storage blocked: the choice simply isn't remembered
  }
}

function writeEnabled(enabled: boolean) {
  try {
    window.localStorage.setItem(AUTO_CHAIN_STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // not remembered across reloads, still applies to this page view
  }
  for (const listener of listeners) listener();
}

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * "Auto-chain": when the planner finishes cleanly, start synth by itself. Only a clean `done` chains —
 * a failed or cancelled planner stays manual so the (long, costly) synth run is a conscious choice.
 * The planner's lock needs a moment to clear, so this waits for the run registry instead of guessing.
 */
export function useAutoChain(startSynth: () => Promise<void>, report: (message: string) => void) {
  const enabled = useSyncExternalStore(subscribe, readEnabled, () => false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const onPlannerFinished = useCallback(
    async (status: string) => {
      if (!enabled || status !== "done") return;
      const deadline = Date.now() + AUTO_CHAIN_MAX_WAIT_MS;
      while (mounted.current && Date.now() < deadline) {
        const active = await fetchPipelineActive();
        if (active !== null && active.planner === null) {
          if (mounted.current) await startSynth();
          return;
        }
        await pause(AUTO_CHAIN_POLL_MS);
      }
      if (mounted.current)
        report("Auto-chain: the planner finished, but synth could not start — it is still locked.");
    },
    [enabled, startSynth, report],
  );

  return { enabled, setEnabled: writeEnabled, onPlannerFinished };
}
