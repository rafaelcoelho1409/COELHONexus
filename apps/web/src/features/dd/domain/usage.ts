/** Docs Distiller usage — LLM calls and tokens per run, per node, per model and combined. Models are named
 * exactly as the LLM endpoint reported them. */

import type { ModelCounts, NodeUsage, UsageCounts, UsagePayload } from "../entities";
import type { RunStage } from "../params";
import { fmtInt } from "./format";
import type { NodeMetric } from "./node-details";
import { percent } from "./overview/read";
import { STAGE_NODES } from "./stages";

const NO_COUNTS: UsageCounts = { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 };
const NO_NODE: NodeUsage = { ...NO_COUNTS, by_model: {} };

export function hasUsage(payload: UsagePayload | null | undefined): payload is UsagePayload {
  return (payload?.total.calls ?? 0) > 0;
}

const add = (a: UsageCounts, b: UsageCounts): UsageCounts => ({
  calls: a.calls + b.calls,
  tokens_in: a.tokens_in + b.tokens_in,
  tokens_out: a.tokens_out + b.tokens_out,
  reasoning_tokens: a.reasoning_tokens + b.reasoning_tokens,
  errors: a.errors + b.errors,
});

const addModel = (a: ModelCounts, b: ModelCounts): ModelCounts => ({
  calls: a.calls + b.calls,
  tokens_in: a.tokens_in + b.tokens_in,
  tokens_out: a.tokens_out + b.tokens_out,
  reasoning_tokens: a.reasoning_tokens + b.reasoning_tokens,
});

/** `into` plus `from`, model by model. */
function addModels(
  into: Readonly<Record<string, ModelCounts>>,
  from: Readonly<Record<string, ModelCounts>>,
): Record<string, ModelCounts> {
  const merged = { ...into };
  for (const [model, counts] of Object.entries(from)) {
    const soFar = merged[model];
    merged[model] = soFar ? addModel(soFar, counts) : counts;
  }
  return merged;
}

/** The sum of several runs' usage (one planner run plus every chapter run, for instance). */
export function mergeUsage(payloads: ReadonlyArray<UsagePayload | null | undefined>): UsagePayload {
  const merged: UsagePayload = { total: NO_COUNTS, by_node: {} };
  for (const payload of payloads) {
    if (!payload) continue;
    merged.total = add(merged.total, payload.total);
    for (const [nodeId, node] of Object.entries(payload.by_node)) {
      const soFar = merged.by_node[nodeId] ?? NO_NODE;
      merged.by_node[nodeId] = {
        ...add(soFar, node),
        by_model: addModels(soFar.by_model, node.by_model),
      };
    }
  }
  return merged;
}

export type ModelUsageRow = ModelCounts & { model: string };

/** Models that answered at least one call, busiest first. */
function modelRows(byModel: Readonly<Record<string, ModelCounts>>): ModelUsageRow[] {
  return Object.entries(byModel)
    .filter(([, counts]) => counts.calls > 0)
    .map(([model, counts]) => ({ ...counts, model }))
    .sort((a, b) => b.calls - a.calls || a.model.localeCompare(b.model));
}

/** One node's calls and tokens per model. The model id is shown exactly as the endpoint returned it. */
export function nodeModelRows(payload: UsagePayload | null | undefined, nodeId: string): ModelUsageRow[] {
  return modelRows(payload?.by_node[nodeId]?.by_model ?? {});
}

/** The whole run's calls and tokens per model, every node added up. */
export function payloadModelRows(payload: UsagePayload | null | undefined): ModelUsageRow[] {
  const all = Object.values(payload?.by_node ?? {}).reduce<Record<string, ModelCounts>>(
    (merged, node) => addModels(merged, node.by_model),
    {},
  );
  return modelRows(all);
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

/** One figure of the whole pipeline, with the part of it the planner and the synth chapters each used. */
export type PipelineFigure = { label: string; value: string; planner: string; synth: string };

const PIPELINE_FIELDS: ReadonlyArray<{
  label: string;
  pick: (counts: UsageCounts) => number;
  /** Shown only when something was counted (reasoning tokens exist for some models only). */
  optional?: boolean;
}> = [
  { label: "LLM calls", pick: (counts) => counts.calls },
  { label: "Input tokens", pick: (counts) => counts.tokens_in },
  { label: "Output tokens", pick: (counts) => counts.tokens_out },
  { label: "Reasoning tokens", pick: (counts) => counts.reasoning_tokens, optional: true },
];

/** The pipeline's calls and tokens: the planner's run plus every chapter's, each split by stage. */
export function pipelineFigures(planner: UsageCounts, synth: UsageCounts): PipelineFigure[] {
  return PIPELINE_FIELDS.flatMap(({ label, pick, optional }) => {
    const plannerPart = pick(planner);
    const synthPart = pick(synth);
    if (optional && plannerPart + synthPart <= 0) return [];
    return [
      {
        label,
        value: fmtInt(plannerPart + synthPart),
        planner: fmtInt(plannerPart),
        synth: fmtInt(synthPart),
      },
    ];
  });
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
  const top = nodeModelRows(payload, nodeId)[0];
  if (top) metrics.push({ label: "top model", value: top.model, note: `${fmtInt(top.calls)} calls` });
  return metrics;
}

/** Headline figures, usage first: when two lists carry the same label (a node's own "LLM calls" and the exact
 * usage's), the usage one wins and the other is dropped, so no figure appears twice. */
export function mergeMetrics(usage: readonly NodeMetric[], own: readonly NodeMetric[]): NodeMetric[] {
  const taken = new Set(usage.map((metric) => metric.label.toLowerCase()));
  return [...usage, ...own.filter((metric) => !taken.has(metric.label.toLowerCase()))];
}

type UsageTableRow = {
  label: string;
  /** This node's figure, the whole run's, and the node's share of the run. */
  node: string;
  run: string;
  share: string;
};

export type NodeUsageTable = { runLabel: string; rows: UsageTableRow[] };

const share = (part: number, whole: number): string => {
  if (whole <= 0 || part <= 0) return "—";
  const fraction = part / whole;
  return fraction < 0.01 ? "<1%" : percent(fraction);
};

const perCall = (tokens: number, calls: number): number => (calls > 0 ? Math.round(tokens / calls) : 0);

/** The "LLM calls and tokens" table of a node's drawer: what it used next to what the whole run used, and its
 * share. Counts only, no model; null when the node made no call. */
export function nodeUsageTable(
  stage: RunStage,
  payload: UsagePayload | null | undefined,
  nodeId: string,
): NodeUsageTable | null {
  const node = payload?.by_node[nodeId];
  if (!payload || !node || node.calls <= 0) return null;
  const run = payload.total;
  const counted = (label: string, pick: (counts: UsageCounts) => number): UsageTableRow => ({
    label,
    node: fmtInt(pick(node)),
    run: fmtInt(pick(run)),
    share: share(pick(node), pick(run)),
  });
  const average = (label: string, pick: (counts: UsageCounts) => number): UsageTableRow => ({
    label,
    node: fmtInt(perCall(pick(node), node.calls)),
    run: fmtInt(perCall(pick(run), run.calls)),
    share: "—",
  });

  const rows = [counted("Calls", (counts) => counts.calls)];
  if (run.errors > 0) rows.push(counted("Failed calls", (counts) => counts.errors));
  rows.push(counted("Input tokens", (counts) => counts.tokens_in));
  rows.push(counted("Output tokens", (counts) => counts.tokens_out));
  if (run.reasoning_tokens > 0) rows.push(counted("Reasoning tokens", (counts) => counts.reasoning_tokens));
  rows.push(average("Input per call", (counts) => counts.tokens_in));
  rows.push(average("Output per call", (counts) => counts.tokens_out));
  return { runLabel: stage === "planner" ? "Whole run" : "Whole chapter", rows };
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
