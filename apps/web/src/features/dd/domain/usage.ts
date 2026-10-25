/** Docs Distiller usage — LLM calls and tokens per run, per node and combined. Counts only: which model
 * answered is deliberately not surfaced. */

import type { UsageCounts, UsagePayload } from "../entities";
import type { RunStage } from "../params";
import { fmtInt } from "./format";
import type { NodeMetric } from "./node-details";
import { STAGE_NODES } from "./stages";

const NO_COUNTS: UsageCounts = { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0 };

export function hasUsage(payload: UsagePayload | null | undefined): payload is UsagePayload {
  return (payload?.total.calls ?? 0) > 0;
}

const add = (a: UsageCounts, b: UsageCounts): UsageCounts => ({
  calls: a.calls + b.calls,
  tokens_in: a.tokens_in + b.tokens_in,
  tokens_out: a.tokens_out + b.tokens_out,
  reasoning_tokens: a.reasoning_tokens + b.reasoning_tokens,
});

/** The sum of several runs' usage (one planner run plus every chapter run, for instance). */
export function mergeUsage(payloads: ReadonlyArray<UsagePayload | null | undefined>): UsagePayload {
  const merged: UsagePayload = { total: NO_COUNTS, by_node: {} };
  for (const payload of payloads) {
    if (!payload) continue;
    merged.total = add(merged.total, payload.total);
    for (const [nodeId, counts] of Object.entries(payload.by_node)) {
      merged.by_node[nodeId] = add(merged.by_node[nodeId] ?? NO_COUNTS, counts);
    }
  }
  return merged;
}

export type UsageNodeRow = UsageCounts & { nodeId: string; label: string };

/** Nodes that made at least one call, busiest first, named as the node board names them. */
export function usageRows(stage: RunStage, payload: UsagePayload): UsageNodeRow[] {
  const labels = new Map(STAGE_NODES[stage].map((node) => [node.id, node.label]));
  return Object.entries(payload.by_node)
    .filter(([, counts]) => counts.calls > 0)
    .map(([nodeId, counts]) => ({ ...counts, nodeId, label: labels.get(nodeId) ?? nodeId }))
    .sort((a, b) => b.calls - a.calls);
}

/** Calls and tokens as labelled figures; reasoning tokens only when there were any. */
export function usageFigures(counts: UsageCounts): Array<{ label: string; value: string }> {
  const figures = [
    { label: "calls", value: fmtInt(counts.calls) },
    { label: "input tokens", value: fmtInt(counts.tokens_in) },
    { label: "output tokens", value: fmtInt(counts.tokens_out) },
  ];
  if (counts.reasoning_tokens > 0)
    figures.push({ label: "reasoning tokens", value: fmtInt(counts.reasoning_tokens) });
  return figures;
}

/** "12 calls · 3,400 input tokens · 800 output tokens" */
export function usageLine(counts: UsageCounts): string {
  return usageFigures(counts)
    .map((figure) => `${figure.value} ${figure.label}`)
    .join(" · ");
}

/** What one node used, for its drawer; empty when the node made no call. */
export function nodeUsageMetrics(payload: UsagePayload | null | undefined, nodeId: string): NodeMetric[] {
  const counts = payload?.by_node[nodeId];
  if (!counts || counts.calls <= 0) return [];
  const metrics: NodeMetric[] = [
    { label: "LLM calls", value: fmtInt(counts.calls), note: "exact usage" },
    { label: "input tokens", value: fmtInt(counts.tokens_in) },
    { label: "output tokens", value: fmtInt(counts.tokens_out) },
  ];
  if (counts.reasoning_tokens > 0)
    metrics.push({ label: "reasoning tokens", value: fmtInt(counts.reasoning_tokens) });
  return metrics;
}

// ============================================================
// Plan health
// ============================================================

export type PlanHealthView = {
  status: "unknown" | "healthy" | "degraded";
  /** Why the plan was flagged (empty when healthy), plus things worth knowing either way. */
  reasons: string[];
};

const FALLBACK_DISTILL_PCT = 40;
const FALLBACK_ASSIGN_PCT = 50;

/** The headline flag and, in words, the signals behind it, from the plan's `pipeline_health`. */
export function planHealthView(degraded: boolean | null, health: Record<string, unknown>): PlanHealthView {
  const read = (key: string): number => {
    const value = health[key];
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
  };
  const reasons: string[] = [];
  if (health.chapter_propose_fallback_used === true) {
    reasons.push("Chapter proposals fell back to deterministic titles.");
  }
  if (read("doc_distill_fallback_pct") >= FALLBACK_DISTILL_PCT) {
    reasons.push(`${read("doc_distill_fallback_pct")}% of documents used the distill fallback.`);
  }
  if (read("chapter_assign_fallback_pct") >= FALLBACK_ASSIGN_PCT) {
    reasons.push(
      `${read("chapter_assign_fallback_pct")}% of documents used the lexical assignment fallback.`,
    );
  }
  const chapters = read("chapter_select_n_chapters");
  if (chapters > 0 && chapters < 3)
    reasons.push(`Only ${chapters} chapter${chapters === 1 ? "" : "s"} survived selection.`);
  if (health.oversized_chapter === true) {
    reasons.push(`One chapter holds ${read("chapter_largest_share_pct")}% of all sources.`);
  }
  if (read("chapter_select_unplaced_docs") > 0) {
    reasons.push(
      `${read("chapter_select_unplaced_docs")} documents were lost between assignment and the plan.`,
    );
  }
  if (read("off_topic_llm_errors") > 0)
    reasons.push(`${read("off_topic_llm_errors")} off-topic judge calls failed.`);
  if (degraded === null) return { status: "unknown", reasons: [] };
  return { status: degraded ? "degraded" : "healthy", reasons };
}
