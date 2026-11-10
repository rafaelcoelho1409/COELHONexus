"use client";

/** The run's LLM usage: calls and tokens in all, then by model. Model names are the server's, verbatim — runtime data shown
 * because the owner asked for it on usage screens (see AGENTS.md); never written into source, copy or fixtures. */

import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";

import { fetchAskUsage, fetchPipelineUsage } from "../api";
import { fmtInt, usageModelRows } from "../ask";
import { SideSheet } from "./side-sheet";

type PipelineUsageSheetProps = {
  live?: boolean;
  onClose: () => void;
  title?: string;
  subtitle?: string;
  emptyMessage?: string;
} & (
  | { threadId: string | null; bookId?: never; runId?: never }
  | { bookId: string; runId: string | null; threadId?: never }
);

export function PipelineUsageSheet(props: PipelineUsageSheetProps): JSX.Element {
  const {
    threadId,
    bookId,
    runId,
    live = false,
    onClose,
    title = "Pipeline LLM usage",
    subtitle = "Calls and token counts for this run.",
    emptyMessage = "No run yet. Usage appears here once a run has started.",
  } = props;
  const isAskUsage = threadId !== undefined;
  const hasIdentifier = isAskUsage ? threadId !== null : bookId !== undefined && runId !== null;
  const query = useQuery({
    queryKey: isAskUsage
      ? (["bd", "ask", "usage", threadId] as const)
      : (["bd", "pipeline", "usage", bookId, runId] as const),
    queryFn: () =>
      isAskUsage ? fetchAskUsage(threadId ?? "") : fetchPipelineUsage(bookId ?? "", runId ?? ""),
    enabled: hasIdentifier,
    refetchInterval: live ? 3_000 : false,
    retry: false,
  });
  const usage = query.data ?? null;
  const total = usage?.total ?? null;
  const rows = usageModelRows(usage);
  const figures = total
    ? [
        { label: "Calls", value: fmtInt(total.calls) },
        { label: "Input tokens", value: fmtInt(total.tokens_in) },
        { label: "Output tokens", value: fmtInt(total.tokens_out) },
        ...(total.reasoning_tokens > 0
          ? [{ label: "Reasoning tokens", value: fmtInt(total.reasoning_tokens) }]
          : []),
      ]
    : [];

  return (
    <SideSheet title={title} closeLabel="Close usage" onClose={onClose} subtitle={subtitle}>
      {query.isError ? (
        <p role="alert" className="text-sm text-destructive-text">
          LLM usage could not be loaded.
        </p>
      ) : query.isPending && hasIdentifier ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading LLM usage…
        </p>
      ) : !hasIdentifier ? (
        <p className="text-sm text-muted-foreground">{emptyMessage}</p>
      ) : total && total.calls > 0 ? (
        <div className="flex flex-col gap-4">
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {figures.map((figure) => (
              <div key={figure.label} className="min-w-0 rounded-sm border border-border px-3 py-2">
                <dt className="text-xs text-muted-foreground">{figure.label}</dt>
                <dd className="tabular text-lg font-semibold">{figure.value}</dd>
              </div>
            ))}
          </dl>
          {rows.length > 0 ? (
            <section
              // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
              tabIndex={0}
              aria-label="Usage by model"
              className="overflow-x-auto rounded-sm border border-border"
            >
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">By model</caption>
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th scope="col" className="px-3 py-2 font-medium">
                      Model
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
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      Reasoning
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.model} className="border-b border-border last:border-b-0">
                      <th
                        scope="row"
                        className="px-3 py-1.5 text-left font-mono text-xs font-normal break-all"
                      >
                        {row.model}
                      </th>
                      <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.calls)}</td>
                      <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.tokens_in)}</td>
                      <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.tokens_out)}</td>
                      <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.reasoning_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          {usage ? "No LLM usage recorded yet." : "Usage is unavailable for this run."}
        </p>
      )}
    </SideSheet>
  );
}
