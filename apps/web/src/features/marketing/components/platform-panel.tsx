"use client";

import { cn } from "@/shared/lib/utils";

import { formatCount } from "../domain";
import { usePlatformStatus } from "../hooks/use-platform-status";
import { NO_VALUE, PLATFORM_REFRESH_MS } from "../params";

type Row = { label: string; value: string; busy: boolean };

/** Live readout of the platform this page is served from — real API reads, no hard-coded figures. */
export function PlatformPanel() {
  const { api, documentationSets, videos, activeRuns } = usePlatformStatus();

  const apiState = api.isPending ? "checking" : api.data === true ? "operational" : "unreachable";
  const rows: Row[] = [
    {
      label: "Documentation sets",
      value: formatCount(documentationSets.data, documentationSets.isPending ? "…" : NO_VALUE),
      busy: documentationSets.isPending,
    },
    {
      label: "Indexed videos",
      value: formatCount(videos.data, videos.isPending ? "…" : NO_VALUE),
      busy: videos.isPending,
    },
    {
      label: "Runs in progress",
      value: formatCount(activeRuns.data, activeRuns.isPending ? "…" : NO_VALUE),
      busy: activeRuns.isPending,
    },
  ];

  return (
    <section aria-labelledby="platform-panel-title" className="border border-border bg-card">
      <header className="flex items-center justify-between gap-4 border-b border-border px-5 py-3">
        <h2 id="platform-panel-title" className="mono-label">
          Platform
        </h2>
        <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.08em]" role="status">
          <span
            aria-hidden="true"
            className={cn(
              "size-2 rounded-full",
              apiState === "operational" && "animate-status bg-success",
              apiState === "checking" && "bg-neutral-8",
              apiState === "unreachable" && "bg-destructive",
            )}
          />
          <span
            className={cn(
              apiState === "operational" && "text-success-text",
              apiState === "checking" && "text-muted-foreground",
              apiState === "unreachable" && "text-primary-text",
            )}
          >
            {apiState === "operational" && "API operational"}
            {apiState === "checking" && "Checking API"}
            {apiState === "unreachable" && "API unreachable"}
          </span>
        </p>
      </header>
      <dl className="divide-y divide-border">
        {rows.map((row) => (
          <div
            key={row.label}
            className="flex items-baseline justify-between gap-4 px-5 py-4"
            aria-busy={row.busy}
          >
            <dt className="text-sm text-muted-foreground">{row.label}</dt>
            <dd className="tabular font-mono text-xl text-foreground">{row.value}</dd>
          </div>
        ))}
      </dl>
      <footer className="border-t border-border px-5 py-3 text-xs text-muted-foreground">
        Read from the running API every {PLATFORM_REFRESH_MS / 1000} seconds.
      </footer>
    </section>
  );
}
