/** Docs Distiller ingestion usage — calls and tokens of one run, by agent, by model or both. The LLM counters are
 * kept per agent (`page_auditor`, `page_repair`); each graph node owns the counters of the agent it runs. */

import type { ModelCounts, NodeUsage, UsagePayload } from "../entities";
import type { IngestionNodeId } from "./ingestion-progress";

export type IngestionUsageMode = "agent" | "model" | "agent_model";
export type IngestionUsageRow = ModelCounts & {
  key: string;
  agent: string | null;
  provider: string | null;
  model: string | null;
};

const COUNTERS_BY_NODE: Readonly<Record<IngestionNodeId, readonly string[]>> = {
  audit_pages: ["audit_pages", "page_auditor"],
  normalize_pages: ["normalize_pages", "page_repair"],
  publish: ["publish"],
};

const AGENT_LABELS: Readonly<Record<string, string>> = {
  audit_pages: "Audit pages",
  page_auditor: "Page auditor",
  normalize_pages: "Normalize pages",
  page_repair: "Page repair",
  publish: "Publish pages",
};

const ZERO_NODE_USAGE: NodeUsage = {
  calls: 0,
  tokens_in: 0,
  tokens_out: 0,
  reasoning_tokens: 0,
  errors: 0,
  by_model: {},
};

function addModelCounts(left: ModelCounts, right: ModelCounts): ModelCounts {
  return {
    calls: left.calls + right.calls,
    tokens_in: left.tokens_in + right.tokens_in,
    tokens_out: left.tokens_out + right.tokens_out,
    reasoning_tokens: left.reasoning_tokens + right.reasoning_tokens,
  };
}

/** Busiest first: LLM calls, then input tokens, then output tokens, each descending; a full tie returns 0 so the
 * caller can chain its own tiebreak. */
function compareUsageDescending(left: ModelCounts, right: ModelCounts): number {
  return right.calls - left.calls || right.tokens_in - left.tokens_in || right.tokens_out - left.tokens_out;
}

/** The endpoint reports `provider/model`; a bare id has no provider to show. */
function splitModelId(modelId: string): { provider: string; model: string } {
  const separator = modelId.indexOf("/");
  if (separator < 0) return { provider: "—", model: modelId };
  return { provider: modelId.slice(0, separator), model: modelId.slice(separator + 1) };
}

function countersForNode(usage: UsagePayload | undefined, nodeId?: IngestionNodeId) {
  const included = nodeId === undefined ? null : new Set(COUNTERS_BY_NODE[nodeId]);
  return Object.entries(usage?.by_node ?? {}).filter(
    ([counterId]) => included === null || included.has(counterId),
  );
}

/** The table behind the usage sheets: whole run, or one node's counters when `nodeId` is given. */
export function ingestionUsageRows(
  usage: UsagePayload | undefined,
  mode: IngestionUsageMode,
  nodeId?: IngestionNodeId,
): IngestionUsageRow[] {
  const counters = countersForNode(usage, nodeId);

  if (mode === "agent") {
    return counters
      .filter(([, counts]) => counts.calls > 0)
      .map(([counterId, counts]) => ({
        key: `agent:${counterId}`,
        agent: AGENT_LABELS[counterId] ?? counterId,
        provider: null,
        model: null,
        calls: counts.calls,
        tokens_in: counts.tokens_in,
        tokens_out: counts.tokens_out,
        reasoning_tokens: counts.reasoning_tokens,
      }))
      .sort(
        (left, right) =>
          compareUsageDescending(left, right) || (left.agent ?? "").localeCompare(right.agent ?? ""),
      );
  }

  if (mode === "agent_model") {
    return counters
      .flatMap(([counterId, counts]) =>
        Object.entries(counts.by_model)
          .filter(([, modelCounts]) => modelCounts.calls > 0)
          .map(([modelId, modelCounts]) => ({
            key: `agent-model:${counterId}:${modelId}`,
            agent: AGENT_LABELS[counterId] ?? counterId,
            ...splitModelId(modelId),
            ...modelCounts,
          })),
      )
      .sort(
        (left, right) =>
          compareUsageDescending(left, right) ||
          (left.agent ?? "").localeCompare(right.agent ?? "") ||
          (left.provider ?? "").localeCompare(right.provider ?? "") ||
          (left.model ?? "").localeCompare(right.model ?? ""),
      );
  }

  const byModel = new Map<string, ModelCounts>();
  for (const [, counts] of counters) {
    for (const [modelId, modelCounts] of Object.entries(counts.by_model)) {
      const previous = byModel.get(modelId);
      byModel.set(modelId, previous ? addModelCounts(previous, modelCounts) : modelCounts);
    }
  }
  return [...byModel.entries()]
    .filter(([, counts]) => counts.calls > 0)
    .map(([modelId, counts]) => ({
      key: `model:${modelId}`,
      agent: null,
      ...splitModelId(modelId),
      ...counts,
    }))
    .sort(
      (left, right) =>
        compareUsageDescending(left, right) ||
        (left.provider ?? "").localeCompare(right.provider ?? "") ||
        (left.model ?? "").localeCompare(right.model ?? ""),
    );
}

/** One node's calls and tokens (its agents' counters added up); null when none of them has been counted. */
export function nodeUsageForIngestionStep(
  usage: UsagePayload | undefined,
  nodeId: IngestionNodeId,
): NodeUsage | null {
  const counters = COUNTERS_BY_NODE[nodeId]
    .map((counterId) => usage?.by_node[counterId])
    .filter((counter): counter is NodeUsage => counter !== undefined);
  if (counters.length === 0) return null;

  return counters.reduce<NodeUsage>((total, counter) => {
    const byModel = { ...total.by_model };
    for (const [model, modelCounts] of Object.entries(counter.by_model)) {
      const previous = byModel[model];
      byModel[model] = previous ? addModelCounts(previous, modelCounts) : modelCounts;
    }
    return {
      calls: total.calls + counter.calls,
      tokens_in: total.tokens_in + counter.tokens_in,
      tokens_out: total.tokens_out + counter.tokens_out,
      reasoning_tokens: total.reasoning_tokens + counter.reasoning_tokens,
      errors: total.errors + counter.errors,
      by_model: byModel,
    };
  }, ZERO_NODE_USAGE);
}
