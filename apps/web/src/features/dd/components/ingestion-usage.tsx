"use client";

import { ChartNoAxesColumn, X } from "lucide-react";
import { type ReactNode, useId, useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";

import { fmtInt } from "../domain/format";
import {
  type INGESTION_NODES,
  type IngestionNodeId,
  type IngestionNodeStatus,
  ingestionNodeText,
} from "../domain/ingestion-progress";
import { ingestionUsageRows, nodeUsageForIngestionStep } from "../domain/ingestion-usage";
import type { UsageCounts, UsagePayload } from "../entities";
import { useIngestionUsage } from "../hooks/use-dd-data";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { ActionButton } from "./action-button";
import { NodeDetailsList, type StageNodeDetails } from "./stage-parts";
import { SideSheet } from "./side-sheet";
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
  resources,
  status,
  caption,
  usage,
  loading,
  failed,
  hasRun,
  onClose,
}: {
  node: (typeof INGESTION_NODES)[number];
  resources?: StageNodeDetails;
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
      title={node.label}
      subtitle={
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{node.label}</span>
          <span>{ingestionNodeText(status)}</span>
          <span className="font-mono">ingestion · {node.id}</span>
          {caption ? <span>{caption}</span> : null}
        </p>
      }
      closeLabel="Close node details"
      onClose={onClose}
    >
      <Tabs defaultValue="resources" className="w-full">
        <TabsList aria-label="Node details">
          <TabsTrigger value="resources">Resources</TabsTrigger>
          <TabsTrigger value="usage">LLM usage</TabsTrigger>
        </TabsList>
        <TabsContent value="resources">
          {resources ? (
            <section aria-label="Agent, skills, and tools" className="rounded-sm border border-border bg-card p-3">
              <NodeDetailsList details={resources} />
            </section>
          ) : (
            <p className="text-sm text-muted-foreground">No agent, skill, or tool is assigned to this node.</p>
          )}
        </TabsContent>
        <TabsContent value="usage" className="flex flex-col gap-3">
          <h3 className="text-sm font-medium">LLM usage</h3>
          <UsageBreakdown usage={usage} nodeId={node.id} loading={loading} failed={failed} hasRun={hasRun} />
        </TabsContent>
      </Tabs>
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

/** Usage control for the Pages extraction-time selector; shows only the `page_selection` counter. */
export function PageSelectionUsageControl({
  runId,
  live,
  assessed,
  total,
}: {
  runId: string | null;
  live: boolean;
  assessed: number;
  total: number;
}) {
  const [open, setOpen] = useState(false);
  const usage = useIngestionUsage(runId, live && open, open);

  return (
    <>
      <ActionButton onClick={() => setOpen(true)} aria-haspopup="dialog">
        <ChartNoAxesColumn aria-hidden />
        LLM usage
      </ActionButton>
      {open ? (
        <PageSelectionUsageSheet
          runId={runId}
          assessed={assessed}
          total={total}
          usage={usage.data}
          loading={runId !== null && usage.isPending}
          failed={runId !== null && usage.isError}
          hasRun={runId !== null}
          errorMessage={usage.isError ? apiErrorMessage(usage.error) : null}
          onRetry={() => void usage.refetch()}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

function PageSelectionUsageSheet({
  runId,
  assessed,
  total,
  usage,
  loading,
  failed,
  hasRun,
  errorMessage,
  onRetry,
  onClose,
}: {
  runId: string | null;
  assessed: number;
  total: number;
  usage: UsagePayload | undefined;
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
  errorMessage: string | null;
  onRetry: () => void;
  onClose: () => void;
}) {
  return (
    <SideSheet
      title="Page selection LLM usage"
      subtitle={
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {runId ? <span className="font-mono">Run {runId.slice(0, 12)}</span> : null}
          {total > 0 ? <span>{fmtInt(assessed)} / {fmtInt(total)} pages assessed</span> : null}
          <span>Usage counts new model requests; cached assessments and exact duplicates create no calls.</span>
        </p>
      }
      closeLabel="Close usage"
      onClose={onClose}
    >
      <div className="flex flex-col gap-4">
        <PageSelectionUsageBreakdown
          usage={usage}
          loading={loading}
          failed={failed}
          hasRun={hasRun}
          errorMessage={errorMessage}
          onRetry={onRetry}
        />
      </div>
    </SideSheet>
  );
}

function PageSelectionUsageBreakdown({
  usage,
  loading,
  failed,
  hasRun,
  errorMessage,
  onRetry,
}: {
  usage: UsagePayload | undefined;
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
  errorMessage: string | null;
  onRetry: () => void;
}) {
  const attributedCounts = usage?.by_node.page_selection;
  const runCounts = usage?.total;
  const runTotalCounts =
    attributedCounts === undefined &&
    runCounts !== undefined &&
    (runCounts.calls > 0 || runCounts.errors > 0)
      ? runCounts
      : undefined;
  // The counter endpoint returns a zero-valued total with an empty by_node map when a run
  // reused cached page assessments and made no model requests. Keep those zeros visible in
  // the usage summary instead of rendering an empty panel that looks like missing telemetry.
  const counts: UsageCounts | undefined =
    attributedCounts ??
    runTotalCounts ??
    (usage ? { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 } : undefined);
  const modelEntries = Object.entries(attributedCounts?.by_model ?? {}).filter(
    ([, modelCounts]) => modelCounts.calls > 0,
  );
  const noRequestsMessage =
    "No new model requests have been recorded for page selection in this run. " +
    "Cached page assessments and exact duplicates are reused without another call.";
  const emptyMessage = runTotalCounts
    ? "Run totals are shown above, but the API did not attribute them to page selection."
    : attributedCounts && attributedCounts.errors > 0
      ? `No successful selector calls yet; ${fmtInt(attributedCounts.errors)} failed attempt(s) are shown in Errors. ` +
        "Cached assessments and exact duplicates do not create new calls."
      : noRequestsMessage;
  const splitModel = (modelId: string) => {
    const separator = modelId.indexOf("/");
    return separator < 0
      ? ["—", modelId]
      : [modelId.slice(0, separator), modelId.slice(separator + 1)];
  };
  const row = (
    key: string,
    dimensions: string[],
    values: { calls: number; tokens_in: number; tokens_out: number; reasoning_tokens: number },
  ) => ({ key, dimensions, ...values });
  const unattributedModel =
    attributedCounts && attributedCounts.calls > 0 && modelEntries.length === 0
      ? row("model:unattributed", ["—", "Model not reported"], attributedCounts)
      : null;
  const runTotalRow = runTotalCounts
    ? row("run-total:unattributed", ["Run total (attribution unavailable)"], runTotalCounts)
    : null;
  const modes: UsageBreakdownModeOption[] = [
    {
      id: "agent",
      label: "By agent",
      caption: "Usage by agent",
      columns: ["Agent"],
      rows:
        attributedCounts && attributedCounts.calls > 0
          ? [
              row("agent:dd-page-selector", ["DD Page selector"], {
                calls: attributedCounts.calls,
                tokens_in: attributedCounts.tokens_in,
                tokens_out: attributedCounts.tokens_out,
                reasoning_tokens: attributedCounts.reasoning_tokens,
              }),
            ]
          : runTotalRow
            ? [runTotalRow]
            : [],
    },
    {
      id: "model",
      label: "By model",
      caption: "Usage by model",
      columns: ["Provider", "Model"],
      rows: runTotalCounts
        ? [row("run-total:unattributed", ["—", "Run total (attribution unavailable)"], runTotalCounts)]
        : unattributedModel
          ? [unattributedModel]
          : modelEntries.map(([modelId, modelCounts]) =>
              row(`model:${modelId}`, splitModel(modelId), modelCounts),
            ),
    },
    {
      id: "agent_model",
      label: "By agent + model",
      caption: "Usage by agent and model",
      columns: ["Agent", "Provider", "Model"],
      rows: runTotalCounts
        ? [row("run-total:unattributed", ["Run total (attribution unavailable)", "—", "—"], runTotalCounts)]
        : unattributedModel
          ? [
              row(
                unattributedModel.key,
                ["DD Page selector", ...unattributedModel.dimensions],
                unattributedModel,
              ),
            ]
          : modelEntries.map(([modelId, modelCounts]) =>
              row(`agent-model:${modelId}`, ["DD Page selector", ...splitModel(modelId)], modelCounts),
            ),
    },
  ];

  return (
    <UsageBreakdownView
      counts={counts}
      modes={modes}
      defaultMode="agent_model"
      hasData={usage !== undefined}
      hasRun={hasRun}
      loading={loading}
      failed={failed}
      errorMessage={errorMessage}
      onRetry={onRetry}
      noRunMessage="Usage will appear after documentation extraction starts."
      emptyMessage={emptyMessage}
    />
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
