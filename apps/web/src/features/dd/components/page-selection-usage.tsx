"use client";

import { ChartNoAxesColumn } from "lucide-react";
import { useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";

import { fmtInt } from "../domain/format";
import type { UsageCounts, UsagePayload } from "../entities";
import { useExtractionUsage } from "../hooks/use-dd-data";
import { ActionButton } from "./action-button";
import { SideSheet } from "./side-sheet";
import { type UsageBreakdownModeOption, UsageBreakdownView } from "./usage-breakdown";

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
  const usage = useExtractionUsage(runId, live && open, open);

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
          {total > 0 ? (
            <span>
              {fmtInt(assessed)} / {fmtInt(total)} pages assessed
            </span>
          ) : null}
          <span>
            Usage counts new model requests; cached assessments and exact duplicates create no calls.
          </span>
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
    attributedCounts === undefined && runCounts !== undefined && (runCounts.calls > 0 || runCounts.errors > 0)
      ? runCounts
      : undefined;
  // The counter endpoint returns a zero-valued total with an empty by_node map when a run
  // reused cached page assessments and made no model requests. Keep those zeros visible in
  // the usage summary instead of rendering an empty panel that looks like missing telemetry.
  const counts: UsageCounts | undefined =
    attributedCounts ??
    runTotalCounts ??
    (usage ? { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 } : undefined);
  const modelEntries = Object.entries(attributedCounts?.by_model ?? {})
    .filter(([, modelCounts]) => modelCounts.calls > 0)
    .sort(
      ([leftModel, left], [rightModel, right]) =>
        right.calls - left.calls ||
        right.tokens_in - left.tokens_in ||
        right.tokens_out - left.tokens_out ||
        leftModel.localeCompare(rightModel),
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
    return separator < 0 ? ["—", modelId] : [modelId.slice(0, separator), modelId.slice(separator + 1)];
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
