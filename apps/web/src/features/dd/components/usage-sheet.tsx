"use client";

/** LLM usage sheets: calls and tokens of a planner run, and of each synth chapter plus all chapters
 * combined once the whole study is rendered. Counts only. */

import type { ChapterCell } from "../domain/chapters";
import { fmtInt } from "../domain/format";
import { hasUsage, mergeUsage, payloadModelRows, usageFigures, usageRows } from "../domain/usage";
import type { UsagePayload } from "../entities";
import { useChapterUsage, useRunUsage } from "../hooks/use-usage";
import type { RunStage } from "../params";
import { ModelUsageTableView } from "./model-usage-table";
import { SideSheet } from "./side-sheet";

/** The headline figures of one usage payload: calls, input and output tokens. */
function UsageFigures({ usage }: { usage: UsagePayload }) {
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {usageFigures(usage.total).map((figure) => (
        <div key={figure.label} className="rounded-sm border border-border px-3 py-2">
          <dt className="text-xs text-muted-foreground">{figure.label}</dt>
          <dd className="text-lg font-semibold tabular-nums">{figure.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function UsageDetail({ stage, usage }: { stage: RunStage; usage: UsagePayload }) {
  const rows = usageRows(stage, usage);
  return (
    <div className="flex flex-col gap-3">
      <UsageFigures usage={usage} />
      <section
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
        tabIndex={0}
        aria-label="Usage by node"
        className="overflow-x-auto rounded-sm border border-border"
      >
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th scope="col" className="px-3 py-2 font-medium">
                Node
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Calls
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Input tokens
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Output tokens
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.nodeId} className="border-b border-border last:border-b-0">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  {row.label}
                </th>
                <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.calls)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.tokens_in)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.tokens_out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <ModelUsageTableView rows={payloadModelRows(usage)} />
    </div>
  );
}

export function PlannerUsageSheet({
  threadId,
  live,
  onClose,
}: {
  threadId: string | null;
  live: boolean;
  onClose: () => void;
}) {
  const usage = useRunUsage("planner", threadId, { live });
  return (
    <SideSheet title="Planner LLM usage" closeLabel="Close usage" onClose={onClose}>
      <p className="mb-3 text-sm text-muted-foreground">The latest planner run, grouped by node and model.</p>
      {hasUsage(usage) ? (
        <UsageDetail stage="planner" usage={usage} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {usage ? "No LLM usage recorded yet." : "Planner usage is loading or unavailable."}
        </p>
      )}
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
  const allRendered = cells.length > 0 && cells.every((cell) => cell.status === "done");
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
            <UsageDetail stage="synth" usage={combined} />
          </section>
        ) : (
          <p className="text-sm text-muted-foreground">
            The combined total appears after all chapters are done.
          </p>
        )}
        {entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">Chapter usage appears here once a chapter has run.</p>
        ) : (
          entries.map(({ cell, usage }) => (
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
                {hasUsage(usage) ? (
                  <UsageDetail stage="synth" usage={usage} />
                ) : (
                  <p className="text-sm text-muted-foreground">No LLM usage recorded for this chapter yet.</p>
                )}
              </div>
            </details>
          ))
        )}
      </div>
    </SideSheet>
  );
}
