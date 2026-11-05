"use client";

import { ChartNoAxesColumn, ChevronDown } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Button } from "@/shared/ui/button";

import { fmtInt } from "../domain/format";
import { tierLabel } from "../domain/ingestion";
import { nodeModelRows, payloadModelRows } from "../domain/usage";
import type { RunStatus, UsagePayload } from "../entities";
import { useIngestionUsage } from "../hooks/use-dd-data";
import { ModelUsageTableView } from "./model-usage-table";
import { SideSheet } from "./side-sheet";

type Progress = NonNullable<RunStatus["progress"]>;
type Stages = NonNullable<Progress["stages"]>;
type ExtractionStage = NonNullable<Stages["extraction"]>;
type AgenticStage = NonNullable<Stages["agentic"]>;

const NODES = [
  { id: "audit_pages", label: "Audit pages", detail: "Find structure, content, and link issues." },
  { id: "normalize_pages", label: "Normalize pages", detail: "Repair each extracted page with Agentic AI." },
  { id: "publish", label: "Publish pages", detail: "Write the normalized pages and manifest." },
] as const;

const STATUS_TEXT: Record<string, string> = {
  idle: "Waiting",
  waiting: "Waiting",
  queued: "Queued",
  running: "In progress",
  done: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
  aborted: "Stopped",
  downgrade: "Trying another tier",
  cancelling: "Cancelling",
};

function statusText(status: string | null | undefined, terminal: string | null): string {
  if (status && STATUS_TEXT[status]) return STATUS_TEXT[status];
  if (terminal === "failed") return "Skipped";
  if (terminal === "cancelled") return "Stopped";
  return "Waiting";
}

function percent(current: number | null | undefined, total: number | null | undefined): number | null {
  if (!total || total <= 0) return null;
  return Math.min(100, Math.round(((current ?? 0) / total) * 100));
}

function PageProgressBox({
  label,
  description,
  status,
  current,
  total,
  lastUrl,
  action,
}: {
  label: string;
  description: string;
  status: string;
  current: number;
  total: number;
  lastUrl?: string | null;
  action?: ReactNode;
}) {
  const value = percent(current, total);
  return (
    <section aria-label={`${label} progress`} className="min-w-0 rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">{label}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{description}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">{status}</span>
          {action}
        </div>
      </div>
      <div
        role="progressbar"
        aria-label={`${label} progress`}
        aria-valuemin={0}
        aria-valuemax={total || 1}
        aria-valuenow={Math.min(current, total || current)}
        className="mt-3 h-1 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div
          className={`h-full bg-primary ${value === null && status === "In progress" ? "w-1/3 animate-pulse" : ""}`}
          style={value === null ? undefined : { width: `${value}%` }}
        />
      </div>
      <p className="mt-2 text-xs tabular-nums text-muted-foreground">
        {total > 0 ? `${Math.min(current, total)} of ${total} items` : `${current} pages captured`}
      </p>
      {lastUrl ? <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{lastUrl}</p> : null}
    </section>
  );
}

function NodeCard({
  node,
  status,
  current,
  total,
  onSelect,
}: {
  node: (typeof NODES)[number];
  status: string;
  current: number;
  total: number;
  onSelect: () => void;
}) {
  return (
    <li data-status={status} className="min-w-0">
      <button
        type="button"
        onClick={onSelect}
        aria-haspopup="dialog"
        className={`flex min-h-20 w-full flex-col justify-center rounded-sm border bg-background px-3 py-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
          status === "In progress"
            ? "border-running"
            : status === "Failed"
              ? "border-destructive"
              : "border-border"
        }`}
      >
        <span className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-medium">{node.label}</span>
          <span className="text-xs text-muted-foreground">{status}</span>
        </span>
        <span className="mt-1 text-xs text-muted-foreground">{node.detail}</span>
        {total > 0 ? (
          <span className="mt-1 text-xs tabular-nums text-muted-foreground">
            {Math.min(current, total)} of {total} pages
          </span>
        ) : null}
      </button>
    </li>
  );
}

function UsagePanel({
  usage,
  loading,
  failed,
  hasRun,
}: {
  usage: UsagePayload | undefined;
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
}) {
  return (
    <section aria-label="Pipeline usage" className="min-w-0 rounded-md border border-border bg-card p-3">
      <h3 className="text-sm font-medium">Pipeline usage</h3>
      <p className="mt-1 text-xs text-muted-foreground">Agentic ingestion calls and token counts.</p>
      {usage ? (
        <dl className="mt-3 grid grid-cols-1 gap-2">
          {[
            ["LLM calls", usage.total.calls],
            ["Input tokens", usage.total.tokens_in],
            ["Output tokens", usage.total.tokens_out],
            ["Reasoning tokens", usage.total.reasoning_tokens],
            ["Errors", usage.total.errors],
          ].map(([label, value]) => (
            <div key={label} className="rounded-sm border border-border px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums">{fmtInt(Number(value))}</dd>
            </div>
          ))}
        </dl>
      ) : failed ? (
        <p role="status" className="mt-3 text-sm text-warning-text">
          Usage is unavailable right now.
        </p>
      ) : (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          {loading ? "Loading usage…" : hasRun ? "No usage has been reported yet." : "No Agentic AI run yet."}
        </p>
      )}
    </section>
  );
}

function UsageSheet({
  usage,
  nodeId,
  onClose,
}: {
  usage: UsagePayload | undefined;
  nodeId: string;
  onClose: () => void;
}) {
  const selectedNode = NODES.find((node) => node.id === nodeId);
  const rows = nodeId === "all" ? payloadModelRows(usage) : nodeModelRows(usage, nodeId);
  const nodes = Object.entries(usage?.by_node ?? {}).filter(([, counts]) => counts.calls > 0);
  return (
    <SideSheet
      title={selectedNode ? `Ingestion usage · ${selectedNode.label}` : "Ingestion LLM usage"}
      closeLabel="Close ingestion usage"
      onClose={onClose}
    >
      {usage ? (
        <div className="flex flex-col gap-4">
          <section aria-label={selectedNode ? `${selectedNode.label} usage` : "Run totals"}>
            <h3 className="text-sm font-medium">{selectedNode ? selectedNode.label : "Run totals"}</h3>
            <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {[
                ["LLM calls", (selectedNode ? usage.by_node[nodeId] : usage.total)?.calls ?? 0],
                ["Input tokens", (selectedNode ? usage.by_node[nodeId] : usage.total)?.tokens_in ?? 0],
                ["Output tokens", (selectedNode ? usage.by_node[nodeId] : usage.total)?.tokens_out ?? 0],
                [
                  "Reasoning tokens",
                  (selectedNode ? usage.by_node[nodeId] : usage.total)?.reasoning_tokens ?? 0,
                ],
                ["Errors", (selectedNode ? usage.by_node[nodeId] : usage.total)?.errors ?? 0],
              ].map(([label, value]) => (
                <div key={label} className="rounded-sm border border-border px-3 py-2">
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="text-base font-semibold tabular-nums">{fmtInt(Number(value))}</dd>
                </div>
              ))}
            </dl>
          </section>
          {!selectedNode ? (
            <section aria-label="Usage by graph node">
              <h3 className="mb-2 text-sm font-medium">By graph node</h3>
              {nodes.length > 0 ? (
                <ul className="flex flex-col gap-2">
                  {nodes.map(([node, counts]) => (
                    <li key={node} className="rounded-sm border border-border px-3 py-2">
                      <p className="text-sm font-medium">
                        {NODES.find((item) => item.id === node)?.label ?? node}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {fmtInt(counts.calls)} calls · {fmtInt(counts.tokens_in)} input ·{" "}
                        {fmtInt(counts.tokens_out)} output tokens
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">No LLM calls have been recorded.</p>
              )}
            </section>
          ) : null}
          <ModelUsageTableView rows={rows} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Usage details are not available yet.</p>
      )}
    </SideSheet>
  );
}

export function IngestionPipeline({
  runId,
  progress,
  onCancel,
  onDismiss,
  cancelling,
  cancelError,
  statusUnavailable,
}: {
  runId: string | null;
  progress: Progress | null;
  onCancel?: () => void;
  onDismiss?: () => void;
  cancelling?: boolean;
  cancelError?: string | null;
  statusUnavailable?: boolean;
}) {
  const [mode, setMode] = useState<"graph" | "list">("graph");
  const [usageNode, setUsageNode] = useState<string | null>(null);
  const usageQuery = useIngestionUsage(
    runId,
    progress?.status === "running" || progress?.status === "cancelling",
  );
  const stages = progress?.stages ?? null;
  const extraction: ExtractionStage | null = stages?.extraction ?? null;
  const agentic: AgenticStage | null = stages?.agentic ?? null;
  const terminal = ["done", "failed", "cancelled"].includes(progress?.status ?? "");
  const hasRun = runId !== null;
  const extractionStatus = hasRun ? statusText(extraction?.status, progress?.status ?? null) : "Waiting";
  const agenticStatus = hasRun ? statusText(agentic?.status, progress?.status ?? null) : "Waiting";

  return (
    <div className="flex flex-col gap-4">
      {statusUnavailable ? (
        <p role="status" className="rounded-md border border-border bg-card p-3 text-sm text-warning-text">
          Run progress is unavailable. The saved library remains available in Materials.
        </p>
      ) : null}
      <div className="grid grid-cols-1 items-stretch gap-3 lg:grid-cols-2">
        <PageProgressBox
          label="Documentation extraction"
          description={
            extraction?.tier
              ? `Tier: ${tierLabel(extraction.tier)}`
              : "Capture extracted pages in raw storage."
          }
          status={extractionStatus}
          current={extraction?.current ?? 0}
          total={extraction?.total ?? 0}
          lastUrl={extraction?.last_url}
          action={
            onCancel && !terminal ? (
              <Button variant="outline" size="sm" onClick={onCancel} disabled={cancelling}>
                {cancelling ? "Cancelling…" : "Cancel run"}
              </Button>
            ) : null
          }
        />
        <PageProgressBox
          label="Agentic AI normalization"
          description="Audit and repair each prepared page after extraction finishes."
          status={agenticStatus}
          current={agentic?.current ?? 0}
          total={agentic?.total ?? 0}
        />
      </div>
      {cancelling && !terminal ? (
        <p role="status" className="text-sm text-warning-text">
          Cancelling — stopping at a safe page or graph boundary.
        </p>
      ) : null}
      {cancelError ? (
        <p role="alert" className="text-sm text-destructive-text">
          {cancelError}
        </p>
      ) : null}
      {progress?.error ? (
        <p role="alert" className="break-words text-sm text-destructive-text">
          {progress.error}
        </p>
      ) : null}
      {progress?.tier === "post" && !terminal ? (
        <p role="status" className="text-sm text-muted-foreground">
          Preparing page boundaries for Agentic AI.
        </p>
      ) : null}
      {terminal && onDismiss ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2">
          <p className="text-sm" role="status" aria-live="polite">
            Ingestion {statusText(progress?.status, progress?.status ?? null).toLowerCase()}.
          </p>
          <Button variant="outline" size="sm" onClick={onDismiss}>
            Dismiss run
          </Button>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,4fr)_minmax(0,1fr)]">
        <section
          aria-label="Agentic pipeline stages"
          className="min-w-0 rounded-md border border-border bg-card p-3"
        >
          <header className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium">Agentic pipeline</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {hasRun
                  ? `${agenticStatus} · ${agentic?.current ?? 0} of ${agentic?.total ?? 0} pages normalized`
                  : "Agentic AI starts when tier extraction is complete."}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {hasRun ? (
                <Button
                  variant="outline"
                  size="default"
                  onClick={() => setUsageNode("all")}
                  aria-haspopup="dialog"
                >
                  <ChartNoAxesColumn aria-hidden />
                  LLM usage
                </Button>
              ) : null}
              <fieldset className="m-0 flex border-0 p-0">
                <legend className="sr-only">Pipeline shown as</legend>
                {(["graph", "list"] as const).map((view) => (
                  <button
                    key={view}
                    type="button"
                    aria-pressed={mode === view}
                    onClick={() => setMode(view)}
                    className={`min-h-9 border px-3 text-xs first:rounded-l-sm last:rounded-r-sm ${
                      mode === view
                        ? "border-primary bg-primary/10 text-primary-text"
                        : "-ml-px border-border text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {view === "graph" ? "Graph" : "List"}
                  </button>
                ))}
              </fieldset>
            </div>
          </header>
          {mode === "graph" ? (
            <ol aria-label="Agentic pipeline graph" className="mt-3 flex max-w-3xl flex-col gap-1.5">
              {NODES.map((node, index) => {
                const nodeStatus = statusText(agentic?.node_status[node.id], progress?.status ?? null);
                const nodeProgress = agentic?.node_progress[node.id];
                return (
                  <li key={node.id}>
                    <NodeCard
                      node={node}
                      status={nodeStatus}
                      current={nodeProgress?.current ?? 0}
                      total={nodeProgress?.total ?? 0}
                      onSelect={() => setUsageNode(node.id)}
                    />
                    {index < NODES.length - 1 ? (
                      <div className="flex h-6 items-center pl-5 text-muted-foreground" aria-hidden="true">
                        <ChevronDown className="size-4" />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          ) : (
            <ol aria-label="Agentic pipeline nodes" className="mt-3 flex flex-col gap-1.5">
              {NODES.map((node) => {
                const nodeStatus = statusText(agentic?.node_status[node.id], progress?.status ?? null);
                const nodeProgress = agentic?.node_progress[node.id];
                return (
                  <li key={node.id}>
                    <NodeCard
                      node={node}
                      status={nodeStatus}
                      current={nodeProgress?.current ?? 0}
                      total={nodeProgress?.total ?? 0}
                      onSelect={() => setUsageNode(node.id)}
                    />
                  </li>
                );
              })}
            </ol>
          )}
        </section>
        <UsagePanel
          usage={usageQuery.data}
          loading={hasRun && usageQuery.isPending}
          failed={hasRun && usageQuery.isError}
          hasRun={hasRun}
        />
      </div>
      {usageNode ? (
        <UsageSheet usage={usageQuery.data} nodeId={usageNode} onClose={() => setUsageNode(null)} />
      ) : null}
    </div>
  );
}
