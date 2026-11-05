"use client";

import { X } from "lucide-react";
import { type ReactNode, useId, useState } from "react";

import { fmtInt } from "../domain/format";
import {
  type INGESTION_NODES,
  type IngestionNodeId,
  type IngestionNodeStatus,
  ingestionNodeText,
} from "../domain/ingestion-progress";
import {
  type IngestionUsageMode,
  ingestionUsageRows,
  nodeUsageForIngestionStep,
} from "../domain/ingestion-usage";
import type { UsagePayload } from "../entities";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { ActionButton } from "./action-button";
import { ToggleGroup } from "./toggle-group";

const USAGE_MODES: readonly { id: IngestionUsageMode; label: string }[] = [
  { id: "agent", label: "By agent" },
  { id: "model", label: "By model" },
  { id: "agent_model", label: "By agent + model" },
];

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
  const [mode, setMode] = useState<IngestionUsageMode>("agent_model");
  const counts = nodeId ? nodeUsageForIngestionStep(usage, nodeId) : usage?.total;
  const rows = ingestionUsageRows(usage, mode, nodeId);
  const caption =
    mode === "agent" ? "Usage by agent" : mode === "model" ? "Usage by model" : "Usage by agent and model";
  const metrics = [
    ["LLM calls", counts?.calls ?? 0],
    ["Input tokens", counts?.tokens_in ?? 0],
    ["Output tokens", counts?.tokens_out ?? 0],
    ["Reasoning tokens", counts?.reasoning_tokens ?? 0],
    ["Errors", counts?.errors ?? 0],
  ] as const;

  return (
    <>
      {usage ? (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {metrics.map(([label, value]) => (
            <div key={label} className="rounded-sm border border-border px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums">{fmtInt(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {hasRun ? (
        <ToggleGroup legend="Group usage by" options={USAGE_MODES} value={mode} onChange={setMode} />
      ) : null}
      {failed ? (
        <p role="alert" className="text-sm text-destructive-text">
          LLM usage could not be loaded.
        </p>
      ) : loading && !usage ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading LLM usage…
        </p>
      ) : !hasRun ? (
        <p className="text-sm text-muted-foreground">Usage will appear after this node runs.</p>
      ) : rows.length > 0 ? (
        <IngestionUsageTable rows={rows} mode={mode} caption={caption} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {nodeId
            ? "No LLM calls have been recorded for this node. This may be expected for deterministic stages."
            : "No LLM calls have been recorded for this run."}
        </p>
      )}
    </>
  );
}

function IngestionUsageTable({
  rows,
  mode,
  caption,
}: {
  rows: ReturnType<typeof ingestionUsageRows>;
  mode: IngestionUsageMode;
  caption: string;
}) {
  const showAgent = mode !== "model";
  const showModel = mode !== "agent";

  return (
    <section
      aria-label={caption}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
      tabIndex={0}
      className="overflow-x-auto rounded-sm border border-border"
    >
      <table className="w-full min-w-[42rem] border-collapse text-sm">
        <caption className="border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground">
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            {showAgent ? (
              <th scope="col" className="px-3 py-2 font-medium">
                Agent
              </th>
            ) : null}
            {showModel ? (
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
              {showAgent ? (
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  {row.agent}
                </th>
              ) : null}
              {showModel ? (
                <>
                  <td className="break-all px-3 py-1.5 font-mono text-xs">{row.provider}</td>
                  <td className="break-all px-3 py-1.5 font-mono text-xs">{row.model}</td>
                </>
              ) : null}
              <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.calls)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.tokens_in)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.tokens_out)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(row.reasoning_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
