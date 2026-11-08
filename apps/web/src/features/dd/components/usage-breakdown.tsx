"use client";

import { useState } from "react";

import { fmtInt } from "../domain/format";
import { type UsageGroupingMode, usageGroupingRows } from "../domain/usage";
import type { NodeUsage, UsageCounts, UsagePayload } from "../entities";
import type { RunStage } from "../params";
import { ToggleGroup } from "./toggle-group";

export type UsageBreakdownRow = {
  key: string;
  dimensions: string[];
  calls: number;
  tokens_in: number;
  tokens_out: number;
  reasoning_tokens: number;
};

export type UsageBreakdownModeOption = {
  id: string;
  label: string;
  caption: string;
  columns: string[];
  rows: UsageBreakdownRow[];
};

export function UsageBreakdownView({
  counts,
  modes,
  defaultMode,
  hasData,
  hasRun,
  loading,
  failed,
  noRunMessage,
  emptyMessage,
}: {
  counts?:
    | Pick<UsageCounts, "calls" | "tokens_in" | "tokens_out" | "reasoning_tokens" | "errors">
    | NodeUsage;
  modes: readonly UsageBreakdownModeOption[];
  defaultMode: string;
  hasData: boolean;
  hasRun: boolean;
  loading: boolean;
  failed: boolean;
  noRunMessage: string;
  emptyMessage: string;
}) {
  const [mode, setMode] = useState(defaultMode);
  const selected = modes.find((option) => option.id === mode) ?? modes[0];
  const rows = selected?.rows ?? [];

  return (
    <>
      {hasData && counts ? (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {[
            ["LLM calls", counts.calls],
            ["Input tokens", counts.tokens_in],
            ["Output tokens", counts.tokens_out],
            ["Reasoning tokens", counts.reasoning_tokens],
            ["Errors", counts.errors],
          ].map(([label, value]) => (
            <div key={label} className="rounded-sm border border-border px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums">{fmtInt(Number(value))}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {hasRun ? (
        <ToggleGroup
          legend="Group usage by"
          options={modes.map(({ id, label }) => ({ id, label }))}
          value={selected?.id ?? defaultMode}
          onChange={setMode}
        />
      ) : null}
      {failed ? (
        <p role="alert" className="text-sm text-destructive-text">
          LLM usage could not be loaded.
        </p>
      ) : loading && !hasData ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading LLM usage…
        </p>
      ) : !hasRun ? (
        <p className="text-sm text-muted-foreground">{noRunMessage}</p>
      ) : rows.length > 0 && selected ? (
        <section
          aria-label={selected.caption}
          // biome-ignore lint/a11y/noNoninteractiveTabindex: keyboard users must be able to focus and scroll the wide table (WCAG 2.1.1)
          tabIndex={0}
          className="overflow-x-auto rounded-sm border border-border"
        >
          <table className="w-full min-w-[42rem] border-collapse text-sm">
            <caption className="border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground">
              {selected.caption}
            </caption>
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                {selected.columns.map((column) => (
                  <th key={column} scope="col" className="px-3 py-2 font-medium">
                    {column}
                  </th>
                ))}
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
                  {row.dimensions.map((value, index) => {
                    const column = selected.columns[index];
                    if (column === undefined) return null;
                    return index === 0 ? (
                      <th
                        key={`${row.key}:${column}`}
                        scope="row"
                        className="px-3 py-1.5 text-left font-normal"
                      >
                        {value}
                      </th>
                    ) : (
                      <td key={`${row.key}:${column}`} className="break-all px-3 py-1.5 font-mono text-xs">
                        {value}
                      </td>
                    );
                  })}
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.calls)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.tokens_in)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.tokens_out)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.reasoning_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">{emptyMessage}</p>
      )}
    </>
  );
}

const MODES: readonly { id: UsageGroupingMode; label: string; caption: string; columns: string[] }[] = [
  { id: "node", label: "By node", caption: "Usage by node", columns: ["Node"] },
  { id: "model", label: "By model", caption: "Usage by model", columns: ["Provider", "Model"] },
  {
    id: "node_model",
    label: "By node + model",
    caption: "Usage by node and model",
    columns: ["Node", "Provider", "Model"],
  },
];

function splitModelId(modelId: string): [string, string] {
  const separator = modelId.indexOf("/");
  return separator < 0 ? ["—", modelId] : [modelId.slice(0, separator), modelId.slice(separator + 1)];
}

function rowsForMode(
  stage: RunStage,
  usage: UsagePayload | null,
  mode: UsageGroupingMode,
  nodeId?: string,
): UsageBreakdownRow[] {
  return usageGroupingRows(stage, usage, mode, nodeId).map((row) => {
    const model = row.model === null ? [] : splitModelId(row.model);
    const dimensions =
      mode === "node" ? [row.node ?? ""] : mode === "model" ? model : [row.node ?? "", ...model];
    return {
      key: row.key,
      dimensions,
      calls: row.calls,
      tokens_in: row.tokens_in,
      tokens_out: row.tokens_out,
      reasoning_tokens: row.reasoning_tokens,
    };
  });
}

export function UsageBreakdown({
  stage,
  usage,
  nodeId,
  hasRun,
  loading,
  failed,
}: {
  stage: RunStage;
  usage: UsagePayload | null;
  nodeId?: string;
  hasRun: boolean;
  loading: boolean;
  failed: boolean;
}) {
  const counts = nodeId ? usage?.by_node[nodeId] : usage?.total;
  const modes = MODES.map((mode) => ({ ...mode, rows: rowsForMode(stage, usage, mode.id, nodeId) }));
  return (
    <div className="flex flex-col gap-4">
      <UsageBreakdownView
        counts={usage ? counts : undefined}
        modes={modes}
        defaultMode="node_model"
        hasData={usage !== null}
        hasRun={hasRun}
        loading={loading}
        failed={failed}
        noRunMessage="Usage will appear after this run starts."
        emptyMessage={
          nodeId
            ? "No LLM calls have been recorded for this node. This may be expected for deterministic stages."
            : "No LLM calls have been recorded for this run."
        }
      />
    </div>
  );
}
