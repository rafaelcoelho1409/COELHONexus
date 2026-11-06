"use client";

import { useCallback, useEffect, useRef } from "react";

import { fetchPipelineActive } from "../api";
import { AUTO_CHAIN_MAX_WAIT_MS, AUTO_CHAIN_POLL_MS } from "../params";
import { useAutoChainPreference } from "./use-auto-chain-preference";

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * "Auto-chain": when the planner finishes cleanly, start synth by itself. Only a clean `done` chains —
 * a failed or cancelled planner stays manual so the (long, costly) synth run is a conscious choice.
 * The planner's lock needs a moment to clear, so this waits for the run registry instead of guessing.
 * The on/off choice is made in the third navbar (`AutoChainToggle`); this hook only obeys it.
 */
export function useAutoChain(startSynth: () => Promise<void>, report: (message: string) => void) {
  const [enabled] = useAutoChainPreference();
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

  return { onPlannerFinished };
}
