import type { AgenticUsage } from "../entities";
import type { AGENTIC_NODES } from "./agentic-progress";

export type AgenticNodeId = (typeof AGENTIC_NODES)[number]["id"];
type NodeUsage = AgenticUsage["by_node"][string];
type ModelCounts = NodeUsage["by_model"][string];

export type AgenticModelUsageRow = ModelCounts & { agent: string; model: string };
export type AgenticUsageMode = "agent" | "model" | "agent_model";
export type AgenticUsageRow = ModelCounts & {
  key: string;
  agent: string | null;
  provider: string | null;
  model: string | null;
};

const COUNTERS_BY_NODE: Record<AgenticNodeId, readonly string[]> = {
  audit_batch: ["audit_batch", "page_auditor", "finding_verifier"],
  plan_repairs: ["plan_repairs"],
  repair_pages: ["repair_pages", "structure_repair", "text_repair"],
  normalize_metadata: ["normalize_metadata", "metadata_normalizer"],
  classify_content: ["classify_content", "content_classifier"],
  publish: ["publish"],
};

const AGENT_LABELS: Readonly<Record<string, string>> = {
  audit_batch: "Audit pages",
  page_auditor: "Page auditor",
  finding_verifier: "Finding verifier",
  plan_repairs: "Plan repairs",
  repair_pages: "Repair pages",
  structure_repair: "Structure repair",
  text_repair: "Text repair",
  normalize_metadata: "Normalize metadata",
  metadata_normalizer: "Metadata normalizer",
  classify_content: "Select Distill pages",
  content_classifier: "Content classifier",
  publish: "Publish results",
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

/** Busiest first: LLM calls, then input tokens, then output tokens, each descending. A tie on one key falls through
 * to the next; a full tie returns 0 so the caller can chain its own tiebreak. */
export function compareUsageDescending(
  left: Pick<ModelCounts, "calls" | "tokens_in" | "tokens_out">,
  right: Pick<ModelCounts, "calls" | "tokens_in" | "tokens_out">,
): number {
  return right.calls - left.calls || right.tokens_in - left.tokens_in || right.tokens_out - left.tokens_out;
}

function splitModelId(modelId: string): { provider: string; model: string } {
  const separator = modelId.indexOf("/");
  if (separator < 0) return { provider: "—", model: modelId };
  return { provider: modelId.slice(0, separator), model: modelId.slice(separator + 1) };
}

function countersForNode(usage: AgenticUsage | undefined, nodeId?: AgenticNodeId) {
  const includedCounters = nodeId === undefined ? null : new Set(COUNTERS_BY_NODE[nodeId]);
  return Object.entries(usage?.by_node ?? {}).filter(
    ([counterId]) => includedCounters === null || includedCounters.has(counterId),
  );
}

export function agenticUsageRows(
  usage: AgenticUsage | undefined,
  mode: AgenticUsageMode,
  nodeId?: AgenticNodeId,
): AgenticUsageRow[] {
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

export function nodeUsageForAgenticStep(
  usage: AgenticUsage | undefined,
  nodeId: AgenticNodeId,
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

export function agenticModelUsageRows(
  usage: AgenticUsage | undefined,
  nodeId?: AgenticNodeId,
): AgenticModelUsageRow[] {
  return countersForNode(usage, nodeId)
    .flatMap(([counterId, counts]) =>
      Object.entries(counts.by_model)
        .filter(([, modelCounts]) => modelCounts.calls > 0)
        .map(([model, modelCounts]) => ({
          ...modelCounts,
          agent: AGENT_LABELS[counterId] ?? counterId,
          model,
        })),
    )
    .sort(
      (left, right) =>
        left.agent.localeCompare(right.agent) ||
        right.calls - left.calls ||
        left.model.localeCompare(right.model),
    );
}
