"use client";

import { useAutoChainPreference } from "../hooks/use-auto-chain-preference";

/** The Auto-chain choice, drawn in the third navbar as the Book Distiller draws its own ("Run Agentic AI after extraction"). */
export function AutoChainToggle() {
  const [enabled, setEnabled] = useAutoChainPreference();
  return (
    <label
      htmlFor="dd-auto-chain"
      className="flex min-h-9 items-center gap-2 text-xs text-muted-foreground"
      title="When the planner finishes successfully, synth starts by itself. Off by default."
    >
      <input
        id="dd-auto-chain"
        type="checkbox"
        checked={enabled}
        onChange={(event) => setEnabled(event.currentTarget.checked)}
        className="size-4 accent-primary"
      />
      Auto-chain
    </label>
  );
}
