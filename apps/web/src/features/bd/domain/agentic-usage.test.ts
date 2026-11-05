import { describe, expect, it } from "vitest";
import type { AgenticUsage } from "../entities";
import { agenticModelUsageRows, agenticUsageRows, nodeUsageForAgenticStep } from "./agentic-usage";

const usage: AgenticUsage = {
  total: { calls: 7, tokens_in: 900, tokens_out: 180, reasoning_tokens: 20, errors: 0 },
  by_node: {
    page_auditor: {
      calls: 4,
      tokens_in: 400,
      tokens_out: 80,
      reasoning_tokens: 8,
      errors: 0,
      by_model: {
        "vendor-a/model-x": { calls: 4, tokens_in: 400, tokens_out: 80, reasoning_tokens: 8 },
      },
    },
    text_repair: {
      calls: 2,
      tokens_in: 300,
      tokens_out: 60,
      reasoning_tokens: 6,
      errors: 0,
      by_model: {
        "vendor-b/model-y": { calls: 2, tokens_in: 300, tokens_out: 60, reasoning_tokens: 6 },
      },
    },
    structure_repair: {
      calls: 1,
      tokens_in: 200,
      tokens_out: 40,
      reasoning_tokens: 6,
      errors: 0,
      by_model: {
        "vendor-a/model-x": { calls: 1, tokens_in: 200, tokens_out: 40, reasoning_tokens: 6 },
      },
    },
  },
};

describe("Agentic usage", () => {
  it("combines agent counters under the corresponding graph node", () => {
    expect(nodeUsageForAgenticStep(usage, "repair_pages")).toMatchObject({
      calls: 3,
      tokens_in: 500,
      tokens_out: 100,
      reasoning_tokens: 12,
      by_model: {
        "vendor-b/model-y": { calls: 2, tokens_in: 300, tokens_out: 60, reasoning_tokens: 6 },
        "vendor-a/model-x": { calls: 1, tokens_in: 200, tokens_out: 40, reasoning_tokens: 6 },
      },
    });
  });

  it("returns complete per-agent model rows and can scope them to a graph node", () => {
    expect(agenticModelUsageRows(usage)).toEqual([
      {
        agent: "Page auditor",
        model: "vendor-a/model-x",
        calls: 4,
        tokens_in: 400,
        tokens_out: 80,
        reasoning_tokens: 8,
      },
      {
        agent: "Structure repair",
        model: "vendor-a/model-x",
        calls: 1,
        tokens_in: 200,
        tokens_out: 40,
        reasoning_tokens: 6,
      },
      {
        agent: "Text repair",
        model: "vendor-b/model-y",
        calls: 2,
        tokens_in: 300,
        tokens_out: 60,
        reasoning_tokens: 6,
      },
    ]);
    expect(agenticModelUsageRows(usage, "audit_batch")).toHaveLength(1);
  });

  it("groups usage by agent, model, or the agent-model pair", () => {
    expect(agenticUsageRows(usage, "agent")).toEqual([
      {
        key: "agent:page_auditor",
        agent: "Page auditor",
        provider: null,
        model: null,
        calls: 4,
        tokens_in: 400,
        tokens_out: 80,
        reasoning_tokens: 8,
      },
      {
        key: "agent:structure_repair",
        agent: "Structure repair",
        provider: null,
        model: null,
        calls: 1,
        tokens_in: 200,
        tokens_out: 40,
        reasoning_tokens: 6,
      },
      {
        key: "agent:text_repair",
        agent: "Text repair",
        provider: null,
        model: null,
        calls: 2,
        tokens_in: 300,
        tokens_out: 60,
        reasoning_tokens: 6,
      },
    ]);

    expect(agenticUsageRows(usage, "model")).toEqual([
      {
        key: "model:vendor-a/model-x",
        agent: null,
        provider: "vendor-a",
        model: "model-x",
        calls: 5,
        tokens_in: 600,
        tokens_out: 120,
        reasoning_tokens: 14,
      },
      {
        key: "model:vendor-b/model-y",
        agent: null,
        provider: "vendor-b",
        model: "model-y",
        calls: 2,
        tokens_in: 300,
        tokens_out: 60,
        reasoning_tokens: 6,
      },
    ]);

    expect(agenticUsageRows(usage, "agent_model", "repair_pages")).toEqual([
      {
        key: "agent-model:structure_repair:vendor-a/model-x",
        agent: "Structure repair",
        provider: "vendor-a",
        model: "model-x",
        calls: 1,
        tokens_in: 200,
        tokens_out: 40,
        reasoning_tokens: 6,
      },
      {
        key: "agent-model:text_repair:vendor-b/model-y",
        agent: "Text repair",
        provider: "vendor-b",
        model: "model-y",
        calls: 2,
        tokens_in: 300,
        tokens_out: 60,
        reasoning_tokens: 6,
      },
    ]);
  });
});
