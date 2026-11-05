/** Docs Distiller ingestion — the Pipeline tab's picture of a run: the Agentic AI graph's nodes, what state each is in,
 * and the labels the two progress cards print. Pure; the run itself comes from `GET /dd/ingestion/runs/{id}`. */

import type { RunStatus } from "../entities";
import type { NodeView } from "./stages";

type Progress = NonNullable<RunStatus["progress"]>;
type Stages = NonNullable<Progress["stages"]>;
export type AgenticStage = NonNullable<Stages["agentic"]>;

export const INGESTION_NODES = [
  { id: "audit_pages", label: "Audit pages" },
  { id: "normalize_pages", label: "Normalize pages" },
  { id: "publish", label: "Publish pages" },
] as const;

export type IngestionNodeId = (typeof INGESTION_NODES)[number]["id"];
export type IngestionNodeStatus = "pending" | "running" | "done" | "failed" | "stopped";

const PHASE_TEXT: Readonly<Record<string, string>> = {
  idle: "Waiting",
  waiting: "Waiting",
  queued: "Queued",
  running: "Processing",
  done: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
  aborted: "Stopped",
  downgrade: "Trying another tier",
  cancelling: "Stopping",
};

/** What a progress card says about one phase (extraction or Agentic AI): the phase's own status, else what the run's
 * end implies for a phase that never got to run. */
export function phaseStatusText(status: string | null | undefined, runStatus: string | null): string {
  const known = status ? PHASE_TEXT[status] : undefined;
  if (known) return known;
  if (runStatus === "failed") return "Skipped";
  if (runStatus === "cancelled") return "Stopped";
  return "Waiting";
}

export function ingestionPercent(current: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((current / total) * 100)));
}

/** A node left "running" by a run that ended is not running any more: it failed with the run, or stopped with it. */
export function ingestionNodeStatus(
  agentic: AgenticStage | null,
  nodeId: string,
  runStatus: string | null,
): IngestionNodeStatus {
  const status = agentic?.node_status[nodeId];
  if (status === "done") return "done";
  if (status === "failed") return "failed";
  if (status === "running") {
    if (runStatus === "failed") return "failed";
    if (runStatus === "cancelled") return "stopped";
    return "running";
  }
  if (runStatus === "failed" && agentic?.current_node === nodeId) return "failed";
  return "pending";
}

export function ingestionNodeText(status: IngestionNodeStatus): string {
  if (status === "done") return "Complete";
  if (status === "running") return "In progress";
  if (status === "failed") return "Failed";
  if (status === "stopped") return "Stopped";
  return "Waiting";
}

/** The graph's nodes as the planner and synth graphs describe theirs, so the same graph and list draw all three. */
export function ingestionNodeViews(agentic: AgenticStage | null, runStatus: string | null): NodeView[] {
  return INGESTION_NODES.map((node) => ({
    node,
    status: ingestionNodeStatus(agentic, node.id, runStatus),
  }));
}

/** Each page-by-page node's "12 of 40 pages audited", keyed by node id: the line under its name. */
export function ingestionNodeKpis(agentic: AgenticStage | null): Record<string, string> {
  const kpis: Record<string, string> = {};
  for (const node of INGESTION_NODES) {
    const caption = ingestionNodeCaption(agentic, node.id);
    if (caption) kpis[node.id] = caption;
  }
  return kpis;
}

const CAPTION_VERBS: Readonly<Partial<Record<string, string>>> = {
  audit_pages: "audited",
  normalize_pages: "normalized",
};

/** "12 of 40 pages audited" for the nodes that work page by page; null for the rest, or before the total is known. */
export function ingestionNodeCaption(agentic: AgenticStage | null, nodeId: string): string | null {
  const verb = CAPTION_VERBS[nodeId];
  const counts = agentic?.node_progress[nodeId];
  const total = counts?.total ?? 0;
  if (!verb || total <= 0) return null;
  return `${Math.min(counts?.current ?? 0, total)} of ${total} pages ${verb}`;
}

export type PageProgress = { id: string; label: string; processed: number; total: number };

/** The page-by-page nodes with their counts, in graph order. */
export function pageProgress(agentic: AgenticStage | null): PageProgress[] {
  return INGESTION_NODES.flatMap((node) => {
    if (!CAPTION_VERBS[node.id]) return [];
    const counts = agentic?.node_progress[node.id];
    const total = counts?.total ?? 0;
    if (total <= 0) return [];
    return [{ id: node.id, label: node.label, processed: Math.min(counts?.current ?? 0, total), total }];
  });
}

export function pageProgressSummary(
  rows: readonly PageProgress[],
): { completed: number; total: number } | null {
  if (rows.length === 0) return null;
  return { completed: rows.filter((row) => row.processed === row.total).length, total: rows.length };
}
