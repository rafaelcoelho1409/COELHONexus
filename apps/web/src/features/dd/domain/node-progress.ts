import type { StageEvent } from "../entities";
import type { RunStage } from "../params";
import type { NodeStatus } from "./stages";

export type NodeProgress = {
  current: number;
  total: number;
  percent: number;
  label: string;
};

const PLANNER_PROGRESS: Readonly<Record<string, { key: string; label: string }>> = {
  off_topic: { key: "judged", label: "documents judged" },
  doc_distill: { key: "distilled", label: "documents distilled" },
  chapter_assign: { key: "scored", label: "documents scored" },
};

function numberField(value: unknown, key: string): number | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const field = (value as Record<string, unknown>)[key];
  return typeof field === "number" && Number.isFinite(field) ? field : null;
}

function buildProgress(current: number, total: number, label: string): NodeProgress | null {
  if (total <= 0) return null;
  const bounded = Math.max(0, Math.min(total, current));
  return { current: bounded, total, percent: Math.round((bounded / total) * 100), label };
}

/** Real per-item progress emitted by nodes; no percentage is inferred from run status alone. */
export function nodeProgress(
  stage: RunStage,
  nodeId: string,
  events: readonly StageEvent[],
  values: Record<string, unknown>,
  status: NodeStatus,
): NodeProgress | null {
  if (stage === "planner") {
    const fields = PLANNER_PROGRESS[nodeId];
    if (!fields) return null;
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (event?.step !== nodeId || event.kind !== "llm_progress") continue;
      const current = numberField(event, fields.key);
      const total = numberField(event, "total");
      if (current !== null && total !== null) return buildProgress(current, total, fields.label);
    }
    return null;
  }

  if (nodeId !== "sawc_write") return null;

  let startIndex = -1;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event?.step === nodeId && event.kind === "start") {
      startIndex = index;
      break;
    }
  }
  if (startIndex >= 0) {
    const total = numberField(events[startIndex], "n_sections");
    if (total !== null && total > 0) {
      const completed = new Set<string>();
      for (const event of events.slice(startIndex + 1)) {
        if (
          event.step === nodeId &&
          event.kind === "section_done" &&
          typeof event.section_id === "string"
        ) {
          completed.add(event.section_id);
        }
      }
      return buildProgress(completed.size, total, "sections written");
    }
  }

  if (status === "done") {
    const stats = values.sawc_stats;
    const total = numberField(stats, "n_sections");
    const current = numberField(stats, "n_completed");
    if (total !== null && current !== null) return buildProgress(current, total, "sections written");
  }
  return null;
}
