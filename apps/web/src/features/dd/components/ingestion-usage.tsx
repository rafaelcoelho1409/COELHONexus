"use client";

import { X } from "lucide-react";
import { type ReactNode, useId } from "react";

import { fmtInt } from "../domain/format";
import {
  type INGESTION_NODES,
  type IngestionNodeId,
  type IngestionNodeStatus,
  ingestionNodeText,
} from "../domain/ingestion-progress";
import { ingestionUsageRows, nodeUsageForIngestionStep } from "../domain/ingestion-usage";
import type { UsagePayload } from "../entities";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { ActionButton } from "./action-button";
import { type UsageBreakdownModeOption, UsageBreakdownView } from "./usage-breakdown";

export function PipelineUsageTotal({
  usage,
  hasRun,
  loading,
  failed,
}: {
  usage: UsagePayload | undefined;
  hasRun: boolean;
  loading: boolean;
  failed: boolean;
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
              <dd className="text-lg font-semibold tabular-nums">{fmtInt(value)}</dd>
            </div>
          ))}
        </dl>
      ) : failed ? (
        <p role="status" className="mt-3 text-sm text-warning-text">
          Usage is unavailable right now. Open LLM usage to retry.
        </p>
      ) : (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          {loading ? "Loading usage…" : hasRun ? "No usage has been reported yet." : "No Agentic AI run yet."}
        </p>
      )}
    </section>
  );
}

/** A right-hand sheet on the native modal `<dialog>`; render it only while it is open. */
function UsageDialog({
  title,
  subtitle,
  closeLabel,
  onClose,
  children,
}: {
  title: string;
  subtitle: ReactNode;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useModalDialog();
  const titleId = useId();

  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          {subtitle}
        </div>
        <ActionButton iconOnly onClick={onClose} aria-label={closeLabel}>
          <X aria-hidden />
        </ActionButton>
      </header>
      <div className="flex flex-col gap-4 px-5 py-4">{children}</div>
    </dialog>
  );
}

export function IngestionNodeSheet({
  node,
  status,
  caption,
  usage,
  loading,
  failed,
  hasRun,
  onClose,
}: {
  node: (typeof INGESTION_NODES)[number];
  status: IngestionNodeStatus;
  caption: string | null;
  usage: UsagePayload | undefined;
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
  onClose: () => void;
}) {
  return (
    <UsageDialog
      title="Ingestion LLM usage"
      subtitle={
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{node.label}</span>
          <span>{ingestionNodeText(status)}</span>
          <span className="font-mono">ingestion · {node.id}</span>
          {caption ? <span>{caption}</span> : null}
        </p>
      }
      closeLabel="Close node usage"
      onClose={onClose}
    >
      <UsageBreakdown usage={usage} nodeId={node.id} loading={loading} failed={failed} hasRun={hasRun} />
    </UsageDialog>
  );
}

export function IngestionUsageSheet({
  usage,
  loading,
  failed,
  onClose,
}: {
  usage: UsagePayload | undefined;
  loading: boolean;
  failed: boolean;
  onClose: () => void;
}) {
  return (
    <UsageDialog
      title="Ingestion LLM usage"
      subtitle={
        <p className="mt-1 text-sm text-muted-foreground">
          Calls and token counts, grouped by processing node.
        </p>
      }
      closeLabel="Close usage"
      onClose={onClose}
    >
      <UsageBreakdown usage={usage} loading={loading} failed={failed} hasRun />
    </UsageDialog>
  );
}

function UsageBreakdown({
  usage,
  nodeId,
  loading,
  failed,
  hasRun,
}: {
  usage: UsagePayload | undefined;
  nodeId?: IngestionNodeId;
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
}) {
  const counts = nodeId ? nodeUsageForIngestionStep(usage, nodeId) : usage?.total;
  const modes: UsageBreakdownModeOption[] = [
    {
      id: "agent",
      label: "By agent",
      caption: "Usage by agent",
      columns: ["Agent"],
      rows: ingestionUsageRows(usage, "agent", nodeId).map((row) => ({
        key: row.key,
        dimensions: [row.agent ?? ""],
        calls: row.calls,
        tokens_in: row.tokens_in,
        tokens_out: row.tokens_out,
        reasoning_tokens: row.reasoning_tokens,
      })),
    },
    {
      id: "model",
      label: "By model",
      caption: "Usage by model",
      columns: ["Provider", "Model"],
      rows: ingestionUsageRows(usage, "model", nodeId).map((row) => ({
        key: row.key,
        dimensions: [row.provider ?? "—", row.model ?? ""],
        calls: row.calls,
        tokens_in: row.tokens_in,
        tokens_out: row.tokens_out,
        reasoning_tokens: row.reasoning_tokens,
      })),
    },
    {
      id: "agent_model",
      label: "By agent + model",
      caption: "Usage by agent and model",
      columns: ["Agent", "Provider", "Model"],
      rows: ingestionUsageRows(usage, "agent_model", nodeId).map((row) => ({
        key: row.key,
        dimensions: [row.agent ?? "", row.provider ?? "—", row.model ?? ""],
        calls: row.calls,
        tokens_in: row.tokens_in,
        tokens_out: row.tokens_out,
        reasoning_tokens: row.reasoning_tokens,
      })),
    },
  ];

  return (
    <UsageBreakdownView
      counts={usage ? (counts ?? undefined) : undefined}
      modes={modes}
      defaultMode="agent_model"
      hasData={usage !== undefined}
      hasRun={hasRun}
      loading={loading}
      failed={failed}
      noRunMessage="Usage will appear after this node runs."
      emptyMessage={
        nodeId
          ? "No LLM calls have been recorded for this node. This may be expected for deterministic stages."
          : "No LLM calls have been recorded for this run."
      }
    />
  );
}
