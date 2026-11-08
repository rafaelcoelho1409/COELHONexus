"use client";

/** LLM usage sheets: calls and tokens of a planner run, and of each synth chapter plus all chapters
 * combined once the whole study is rendered. Counts only. */

import type { ChapterCell } from "../domain/chapters";
import { hasUsage, mergeUsage } from "../domain/usage";
import { useChapterUsage, useRunUsageState } from "../hooks/use-usage";
import { SideSheet } from "./side-sheet";
import { UsageBreakdown } from "./usage-breakdown";

export function PlannerUsageSheet({
  threadId,
  live,
  onClose,
}: {
  threadId: string | null;
  live: boolean;
  onClose: () => void;
}) {
  const usage = useRunUsageState("planner", threadId, { live });
  return (
    <SideSheet title="Planner LLM usage" closeLabel="Close usage" onClose={onClose}>
      <p className="mb-3 text-sm text-muted-foreground">The latest planner run, grouped by node and model.</p>
      <UsageBreakdown
        stage="planner"
        usage={usage.usage}
        hasRun={threadId !== null}
        loading={usage.loading}
        failed={usage.failed}
      />
    </SideSheet>
  );
}

export function SynthUsageSheet({
  cells,
  live,
  onClose,
}: {
  cells: readonly ChapterCell[];
  live: boolean;
  onClose: () => void;
}) {
  const entries = useChapterUsage(cells, { live });
  const eligible = cells.filter((cell) => cell.synthEligible !== false);
  const allRendered = eligible.length > 0 && eligible.every((cell) => cell.status === "done");
  const combined = mergeUsage(entries.map((entry) => entry.usage));

  return (
    <SideSheet title="Synth LLM usage" closeLabel="Close usage" onClose={onClose}>
      <p className="mb-3 text-sm text-muted-foreground">
        Per chapter while running, plus a combined total once every chapter is done.
      </p>
      <div className="flex flex-col gap-4">
        {allRendered && hasUsage(combined) ? (
          <section aria-label="All chapters combined" className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">All chapters combined</h3>
            <UsageBreakdown stage="synth" usage={combined} hasRun loading={false} failed={false} />
          </section>
        ) : (
          <p className="text-sm text-muted-foreground">
            The combined total appears after all chapters are done.
          </p>
        )}
        {entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">Chapter usage appears here once a chapter has run.</p>
        ) : (
          entries.map(({ cell, usage, loading, failed }) => (
            <details
              key={cell.id}
              open={cell.status === "running"}
              className="rounded-sm border border-border"
            >
              <summary className="flex min-h-9 cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm">
                <span>{cell.title}</span>
                <span className="text-xs text-muted-foreground">
                  {cell.status === "done" ? "rendered" : cell.status}
                </span>
              </summary>
              <div className="border-t border-border px-3 py-3">
                <UsageBreakdown stage="synth" usage={usage} hasRun loading={loading} failed={failed} />
              </div>
            </details>
          ))
        )}
      </div>
    </SideSheet>
  );
}
