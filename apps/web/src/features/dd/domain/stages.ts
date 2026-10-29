/** Docs Distiller planner/synth graphs — node tables, node status and the run-controller helpers. */

import type { StageEvent } from "../entities";
import { type RunStage, THREAD_PREFIX } from "../params";

export type StageNode = {
  id: string;
  label: string;
  /** Checkpoint field that appears in the graph-state values once the node's checkpoint commits
   * (the stream's `done` event, then a state refresh). These are the backend's real state keys. */
  field: string;
  /** The per-node stats dict in the same state ("what happened", shown in the drawer). */
  stats: string;
};

export const PLANNER_NODES: StageNode[] = [
  { id: "corpus_load", label: "Corpus load", field: "raw_files", stats: "corpus_stats" },
  { id: "off_topic", label: "Off-topic filter", field: "relevant_files", stats: "off_topic_stats" },
  { id: "doc_distill", label: "Doc distill", field: "doc_distill_ref", stats: "doc_distill_stats" },
  { id: "chapter_propose", label: "Chapter propose", field: "chapter_proposals_ref", stats: "propose_stats" },
  {
    id: "chapter_assign",
    label: "Chapter assign",
    field: "chapter_doc_assignments_ref",
    stats: "assign_stats",
  },
  { id: "chapter_select", label: "Chapter select", field: "chapter_plan_ref", stats: "select_stats" },
  {
    id: "order_chapters",
    label: "Order chapters",
    field: "chapter_order_ref",
    stats: "order_chapters_stats",
  },
  { id: "plan_write", label: "Plan write", field: "plan_path", stats: "plan_write_stats" },
];

export const SYNTH_NODES: StageNode[] = [
  { id: "outline_sdp", label: "Outline (SDP)", field: "outline_path", stats: "outline_stats" },
  { id: "digest_construct", label: "Digest", field: "digest_path", stats: "digest_stats" },
  { id: "sawc_write", label: "SAWC write", field: "sawc_path", stats: "sawc_stats" },
  { id: "sawc_derive", label: "SAWC derive", field: "derive_stats", stats: "derive_stats" },
  { id: "checklist_eval", label: "Checklist eval", field: "checklist_path", stats: "checklist_stats" },
  { id: "mgsr_replan", label: "MGSR replan", field: "mgsr_path", stats: "mgsr_stats" },
  { id: "render_audit_write", label: "Render + audit", field: "chapter_path", stats: "chapter_stats" },
];

export const STAGE_NODES: Record<RunStage, StageNode[]> = { planner: PLANNER_NODES, synth: SYNTH_NODES };

export type NodeStatus = "done" | "running" | "pending" | "future" | "failed";

/** The node id the values mark as running: the server's scheduled set first (sent only while a thread
 * streams), then the first node that has not committed. */
function deriveNextRunning(
  nodes: StageNode[],
  values: Record<string, unknown>,
  next: readonly string[],
  running: boolean,
): string | null {
  if (!running) return null;
  if (next.length > 0) return next[0] ?? null;
  return nodes.find((node) => !(node.field in values))?.id ?? null;
}

export type StageNodeView = {
  node: StageNode;
  status: NodeStatus;
};

/** Status per node: committed → done, scheduled/first uncommitted → running, not implemented on the
 * server → future. */
export function stageNodeViews(
  nodes: StageNode[],
  values: Record<string, unknown>,
  next: readonly string[],
  running: boolean,
  implemented: ReadonlySet<string>,
): StageNodeView[] {
  const runningId = deriveNextRunning(nodes, values, next, running);
  return nodes.map((node) => {
    if (implemented.size > 0 && !implemented.has(node.id)) return { node, status: "future" };
    if (runningId === node.id) return { node, status: "running" };
    if (node.field in values) return { node, status: "done" };
    return { node, status: "pending" };
  });
}

/** The line over a stage's graph: how far a live run is and what comes next, else how many nodes have committed. */
export function stageCaption(
  views: readonly StageNodeView[],
  progress: string | null,
  next: readonly string[],
): string {
  if (progress)
    return next.length > 0 ? `Progress: ${progress} · Next: ${next.join(", ")}` : `Progress: ${progress}`;
  const done = views.filter((view) => view.status === "done").length;
  return `${done} of ${views.length} nodes done`;
}

/** Progress label for the working pill: "3/8". */
export function stageProgressLabel(
  nodes: StageNode[],
  views: StageNodeView[],
  implemented: ReadonlySet<string>,
): string | null {
  const running = views.find((view) => view.status === "running");
  if (!running) return null;
  const position = nodes.indexOf(running.node) + 1;
  const total = nodes.filter((node) => implemented.size === 0 || implemented.has(node.id)).length;
  return position > 0 && total > 0 ? `${position}/${total}` : null;
}

/** True when every implemented node has committed its checkpoint (also covers a finished run that
 * predates newly added nodes). */
export function stageAllImplementedDone(
  nodes: StageNode[],
  values: Record<string, unknown>,
  implemented: ReadonlySet<string>,
): boolean {
  if (implemented.size === 0) return false;
  return nodes.every((node) => !implemented.has(node.id) || node.field in values);
}

/** Planner start semantics: a completed plan on the server turns Start into Resume (the checkpoint
 * carries the values back into the graph). */
export function plannerStartLabel(values: Record<string, unknown>): "Start" | "Resume" {
  return "plan_path" in values ? "Resume" : "Start";
}

// ============================================================
// Run controller helpers
// ============================================================

export type StagePillState = {
  status: "idle" | "working" | "done" | "failed" | "cancelled";
  label: string | null;
};

export const IDLE_PILL: StagePillState = { status: "idle", label: null };
export const WORKING_PILL: StagePillState = { status: "working", label: null };

/** Pill for a terminal run status as the server reports it (`done` | `failed` | `cancelled`). */
export function terminalPill(status: string): StagePillState {
  if (status === "failed") return { status: "failed", label: "Failed" };
  if (status === "cancelled") return { status: "cancelled", label: "Cancelled" };
  return { status: "done", label: null };
}

/** A planner thread id in the backend's `docs-distiller/{slug}/{uuid}` shape (its "recent runs" query finds
 * threads by that prefix, so any other shape would never be recovered). Made here, before the request, so the
 * stream can be followed and the run stopped from the first click. `randomUUID` only exists in secure contexts
 * (https, localhost) and the cluster is also reached over plain http, so fall back to `random`. */
export function generateThreadId(slug: string, random: () => number = Math.random): string {
  const uuid =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
          const r = Math.floor(random() * 16);
          return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
        });
  return `${THREAD_PREFIX}/${slug}/${uuid}`;
}

/** The newest `limit` events, oldest first — the drawer's activity log keeps a bounded tail. A stream that
 * reconnects replays its history, so an event already in the log (same time, step and kind) is ignored. */
export function appendStageEvent(log: readonly StageEvent[], event: StageEvent, limit: number): StageEvent[] {
  if (
    event.ts !== undefined &&
    log.some((e) => e.ts === event.ts && e.step === event.step && e.kind === event.kind)
  ) {
    return log.slice();
  }
  const next = [...log, event];
  return next.length > limit ? next.slice(next.length - limit) : next;
}
