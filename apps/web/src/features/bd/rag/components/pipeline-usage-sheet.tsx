"use client";

/** The run's LLM usage: calls and tokens in all, then by model. Model names are the server's, verbatim — runtime data shown
 * because the owner asked for it on usage screens (see AGENTS.md); never written into source, copy or fixtures. */

import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { ActionButton } from "../../components/action-button";
import { ToggleGroup } from "../../components/toggle-group";
import type { AgenticUsage } from "../../entities";
import { fetchAskUsage, fetchPipelineUsage } from "../api";
import { fmtInt, usageModelRows } from "../ask";
import type { AskUsage } from "../entities";
import { SideSheet } from "./side-sheet";

type PipelineUsageSheetBaseProps = {
  live?: boolean;
  onClose: () => void;
  title?: string;
  subtitle?: string;
  emptyMessage?: string;
  nodeIds?: readonly string[];
};
type AskUsageSheetProps = PipelineUsageSheetBaseProps & {
  threadId: string | null;
  bookId?: never;
  runId?: never;
};
type RunUsageSheetProps = PipelineUsageSheetBaseProps & {
  bookId: string;
  runId: string | null;
  threadId?: never;
};
type PipelineUsageSheetProps = AskUsageSheetProps | RunUsageSheetProps;

export function PipelineUsageSheet(props: PipelineUsageSheetProps): JSX.Element {
  return "bookId" in props ? (
    <RunUsageSheet {...(props as RunUsageSheetProps)} />
  ) : (
    <AskUsageSheet {...(props as AskUsageSheetProps)} />
  );
}

function AskUsageSheet({
  threadId,
  live = false,
  onClose,
  title = "Pipeline LLM usage",
  subtitle = "Calls and token counts for this run.",
  emptyMessage = "No run yet. Usage appears here once a run has started.",
}: AskUsageSheetProps) {
  const query = useQuery({
    queryKey: ["bd", "ask", "usage", threadId] as const,
    queryFn: () => fetchAskUsage(threadId ?? ""),
    enabled: threadId !== null,
    refetchInterval: live ? 3_000 : false,
    retry: false,
  });
  const usage = query.data ?? null;
  const rows = usageModelRows(usage);

  return (
    <SideSheet title={title} closeLabel="Close usage" onClose={onClose} subtitle={subtitle}>
      {query.isError ? (
        <UsageError message={apiErrorMessage(query.error)} onRetry={() => void query.refetch()} />
      ) : query.isPending && threadId !== null ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading LLM usage…
        </p>
      ) : threadId === null ? (
        <p className="text-sm text-muted-foreground">{emptyMessage}</p>
      ) : usage?.total?.calls ? (
        <div className="flex flex-col gap-4">
          <AskUsageTotals total={usage.total} />
          <UsageModelTable rows={rows} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          {usage ? "No LLM usage recorded yet." : "Usage is unavailable for this run."}
        </p>
      )}
    </SideSheet>
  );
}

type UsageMode = "node" | "model" | "node_model";
type RunUsageRow = {
  key: string;
  node: string | null;
  provider: string | null;
  model: string | null;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  reasoning_tokens: number;
};

const USAGE_MODES: readonly { id: UsageMode; label: string }[] = [
  { id: "node", label: "By node" },
  { id: "model", label: "By model" },
  { id: "node_model", label: "By node + model" },
];

const NODE_LABELS: Readonly<Record<string, string>> = {
  learning_extractor: "Extract lessons",
  evidence_verifier: "Verify evidence",
  chapter_synthesizer: "Synthesize chapters",
  book_synthesizer: "Synthesize book",
  rag_graph_extractor: "Extract knowledge graph",
};

function RunUsageSheet({
  bookId,
  runId,
  live = false,
  onClose,
  title = "Pipeline LLM usage",
  subtitle = "Calls and token counts for this run.",
  emptyMessage = "No run yet. Usage appears here once a run has started.",
  nodeIds,
}: RunUsageSheetProps) {
  const query = useQuery({
    queryKey: ["bd", "pipeline", "usage", bookId, runId] as const,
    queryFn: () => fetchPipelineUsage(bookId, runId ?? ""),
    enabled: runId !== null,
    refetchInterval: live ? 3_000 : false,
    retry: false,
  });
  const [mode, setMode] = useState<UsageMode>("node_model");
  const usage = query.data;
  const nodes = Object.entries(usage?.by_node ?? {}).filter(
    ([nodeId]) => nodeIds === undefined || nodeIds.includes(nodeId),
  );
  const total = nodes.reduce(
    (sum, [, node]) => ({
      calls: sum.calls + node.calls,
      tokens_in: sum.tokens_in + node.tokens_in,
      tokens_out: sum.tokens_out + node.tokens_out,
      reasoning_tokens: sum.reasoning_tokens + node.reasoning_tokens,
      errors: sum.errors + node.errors,
    }),
    { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 },
  );
  const rows = runUsageRows(nodes, mode);

  return (
    <SideSheet title={title} closeLabel="Close usage" onClose={onClose} subtitle={subtitle}>
      {query.isError ? (
        <UsageError message={apiErrorMessage(query.error)} onRetry={() => void query.refetch()} />
      ) : query.isPending && runId !== null ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading LLM usage…
        </p>
      ) : runId === null ? (
        <p className="text-sm text-muted-foreground">{emptyMessage}</p>
      ) : (
        <div className="flex flex-col gap-4">
          <UsageTotals total={total} />
          <ToggleGroup legend="Group usage by" options={USAGE_MODES} value={mode} onChange={setMode} />
          {rows.length > 0 ? (
            <RunUsageTable rows={rows} mode={mode} />
          ) : (
            <p className="text-sm text-muted-foreground">
              No LLM calls have been recorded for this process yet.
            </p>
          )}
        </div>
      )}
    </SideSheet>
  );
}

function runUsageRows(nodes: [string, AgenticUsage["by_node"][string]][], mode: UsageMode): RunUsageRow[] {
  if (mode === "node") {
    return nodes
      .filter(([, counts]) => counts.calls > 0)
      .map(([nodeId, counts]) => ({
        key: `node:${nodeId}`,
        node: NODE_LABELS[nodeId] ?? nodeId,
        provider: null,
        model: null,
        calls: counts.calls,
        tokens_in: counts.tokens_in,
        tokens_out: counts.tokens_out,
        reasoning_tokens: counts.reasoning_tokens,
      }))
      .sort((left, right) => right.calls - left.calls || left.node!.localeCompare(right.node!));
  }

  const rows = nodes.flatMap(([nodeId, counts]) =>
    Object.entries(counts.by_model).map(([modelId, modelCounts]) => {
      const slash = modelId.indexOf("/");
      return {
        key: `${mode}:${nodeId}:${modelId}`,
        node: mode === "node_model" ? (NODE_LABELS[nodeId] ?? nodeId) : null,
        provider: slash < 0 ? "—" : modelId.slice(0, slash),
        model: slash < 0 ? modelId : modelId.slice(slash + 1),
        ...modelCounts,
      };
    }),
  );
  if (mode === "node_model") {
    return rows.sort(
      (left, right) =>
        right.calls - left.calls ||
        left.node!.localeCompare(right.node!) ||
        left.model!.localeCompare(right.model!),
    );
  }

  const byModel = new Map<string, RunUsageRow>();
  for (const row of rows) {
    const key = `${row.provider}/${row.model}`;
    const previous = byModel.get(key);
    byModel.set(
      key,
      previous
        ? {
            ...previous,
            calls: previous.calls + row.calls,
            tokens_in: previous.tokens_in + row.tokens_in,
            tokens_out: previous.tokens_out + row.tokens_out,
            reasoning_tokens: previous.reasoning_tokens + row.reasoning_tokens,
          }
        : { ...row, key, node: null },
    );
  }
  return [...byModel.values()].sort(
    (left, right) => right.calls - left.calls || left.model!.localeCompare(right.model!),
  );
}

function UsageTotals({
  total,
}: {
  total: Pick<AgenticUsage["total"], "calls" | "tokens_in" | "tokens_out" | "reasoning_tokens" | "errors">;
}) {
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {[
        ["LLM calls", total.calls],
        ["Input tokens", total.tokens_in],
        ["Output tokens", total.tokens_out],
        ["Reasoning tokens", total.reasoning_tokens],
        ["Errors", total.errors],
      ].map(([label, value]) => (
        <div key={label} className="rounded-sm border border-border px-3 py-2">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="text-lg font-semibold tabular-nums">{fmtInt(Number(value))}</dd>
        </div>
      ))}
    </dl>
  );
}

function AskUsageTotals({ total }: { total: NonNullable<AskUsage["total"]> }) {
  const figures = [
    { label: "Calls", value: fmtInt(total.calls) },
    { label: "Input tokens", value: fmtInt(total.tokens_in) },
    { label: "Output tokens", value: fmtInt(total.tokens_out) },
    ...(total.reasoning_tokens > 0
      ? [{ label: "Reasoning tokens", value: fmtInt(total.reasoning_tokens) }]
      : []),
  ];
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {figures.map((figure) => (
        <div key={figure.label} className="min-w-0 rounded-sm border border-border px-3 py-2">
          <dt className="text-xs text-muted-foreground">{figure.label}</dt>
          <dd className="tabular text-lg font-semibold">{figure.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function RunUsageTable({ rows, mode }: { rows: RunUsageRow[]; mode: UsageMode }) {
  return (
    <section
      // biome-ignore lint/a11y/noNoninteractiveTabindex: keyboard users need to focus and scroll the wide table (WCAG 2.1.1)
      tabIndex={0}
      aria-label="LLM usage breakdown"
      className="overflow-x-auto rounded-sm border border-border"
    >
      <table className="w-full min-w-[42rem] border-collapse text-sm">
        <caption className="border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground">
          {mode === "node"
            ? "Usage by node"
            : mode === "model"
              ? "Usage by model"
              : "Usage by node and model"}
        </caption>
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            {mode === "node" || mode === "node_model" ? (
              <th scope="col" className="px-3 py-2 font-medium">
                Node
              </th>
            ) : null}
            {mode !== "node" ? (
              <>
                <th scope="col" className="px-3 py-2 font-medium">
                  Provider
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Model
                </th>
              </>
            ) : null}
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
            <tr key={row.key} className="border-b border-border last:border-b-0">
              {mode === "node" || mode === "node_model" ? (
                <th scope="row" className="px-3 py-1.5 text-left font-medium">
                  {row.node}
                </th>
              ) : null}
              {mode !== "node" ? (
                <>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground">{row.provider}</td>
                  <td className="break-all px-3 py-1.5 font-mono text-xs">{row.model}</td>
                </>
              ) : null}
              <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.calls)}</td>
              <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.tokens_in)}</td>
              <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.tokens_out)}</td>
              <td className="tabular px-3 py-1.5 text-right">{fmtInt(row.reasoning_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function UsageModelTable({ rows }: { rows: ReturnType<typeof usageModelRows> }) {
  return rows.length > 0 ? (
    <section aria-label="Usage by model" className="overflow-x-auto rounded-sm border border-border">
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
              <th scope="row" className="break-all px-3 py-1.5 text-left font-mono text-xs font-normal">
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
  ) : (
    <p className="text-sm text-muted-foreground">No LLM usage has been recorded for this run yet.</p>
  );
}

function UsageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-start gap-3">
      <p className="text-sm text-destructive-text">LLM usage could not be loaded — {message}</p>
      <ActionButton onClick={onRetry}>Try again</ActionButton>
    </div>
  );
}
