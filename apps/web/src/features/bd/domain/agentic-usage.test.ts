import { describe, expect, it } from "vitest";
import type { AgenticUsage } from "../entities";
import {
  agenticModelUsageRows,
  agenticUsageRows,
  compareUsageDescending,
  nodeUsageForAgenticStep,
} from "./agentic-usage";

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
        key: "agent:text_repair",
        agent: "Text repair",
        provider: null,
        model: null,
        calls: 2,
        tokens_in: 300,
        tokens_out: 60,
        reasoning_tokens: 6,
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
        key: "agent-model:text_repair:vendor-b/model-y",
        agent: "Text repair",
        provider: "vendor-b",
        model: "model-y",
        calls: 2,
        tokens_in: 300,
        tokens_out: 60,
        reasoning_tokens: 6,
      },
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
    ]);
  });
});

describe("compareUsageDescending", () => {
  const counts = (calls: number, tokens_in: number, tokens_out: number) => ({ calls, tokens_in, tokens_out });
  const sorted = (rows: ReturnType<typeof counts>[]) => [...rows].sort(compareUsageDescending);

  it("puts more LLM calls first", () => {
    expect(sorted([counts(1, 900, 900), counts(5, 1, 1), counts(3, 5, 5)])).toEqual([
      counts(5, 1, 1),
      counts(3, 5, 5),
      counts(1, 900, 900),
    ]);
  });

  it("breaks a calls tie by input tokens, descending", () => {
    expect(sorted([counts(2, 100, 900), counts(2, 300, 1), counts(2, 200, 5)])).toEqual([
      counts(2, 300, 1),
      counts(2, 200, 5),
      counts(2, 100, 900),
    ]);
  });

  it("breaks a calls and input tie by output tokens, descending", () => {
    expect(sorted([counts(2, 100, 10), counts(2, 100, 30), counts(2, 100, 20)])).toEqual([
      counts(2, 100, 30),
      counts(2, 100, 20),
      counts(2, 100, 10),
    ]);
  });

  it("returns 0 on a full tie so the caller's own tiebreak decides", () => {
    expect(compareUsageDescending(counts(2, 100, 10), counts(2, 100, 10))).toBe(0);
  });
});

describe("Agentic usage row order", () => {
  const model = (calls: number, tokens_in: number, tokens_out: number) => ({
    calls,
    tokens_in,
    tokens_out,
    reasoning_tokens: 0,
  });
  const node = (by_model: Record<string, ReturnType<typeof model>>) => ({
    calls: Object.values(by_model).reduce((sum, counts) => sum + counts.calls, 0),
    tokens_in: Object.values(by_model).reduce((sum, counts) => sum + counts.tokens_in, 0),
    tokens_out: Object.values(by_model).reduce((sum, counts) => sum + counts.tokens_out, 0),
    reasoning_tokens: 0,
    errors: 0,
    by_model,
  });
  const tied: AgenticUsage = {
    total: { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 },
    by_node: {
      page_auditor: node({
        "vendor-a/model-x": model(4, 100, 10),
        "vendor-b/model-y": model(4, 100, 30),
        "vendor-c/model-z": model(4, 300, 5),
      }),
      text_repair: node({ "vendor-b/model-y": model(12, 500, 1) }),
    },
  };

  it("sorts every grouping by calls, then input tokens, then output tokens", () => {
    // Both agents have 12 calls and 500 input tokens; the auditor's 45 output tokens beat the repair's 1.
    expect(agenticUsageRows(tied, "agent").map((row) => row.agent)).toEqual(["Page auditor", "Text repair"]);
    expect(agenticUsageRows(tied, "model").map((row) => row.model)).toEqual([
      "model-y", // 16 calls
      "model-z", // 4 calls, 300 in
      "model-x", // 4 calls, 100 in
    ]);
    expect(agenticUsageRows(tied, "agent_model").map((row) => `${row.agent}/${row.model}`)).toEqual([
      "Text repair/model-y", // 12 calls
      "Page auditor/model-z", // 4 calls, 300 in
      "Page auditor/model-y", // 4 calls, 100 in, 30 out
      "Page auditor/model-x", // 4 calls, 100 in, 10 out
    ]);
  });

  it("sorts a graph node's rows the same way", () => {
    expect(agenticUsageRows(tied, "agent_model", "audit_batch").map((row) => row.model)).toEqual([
      "model-z",
      "model-y",
      "model-x",
    ]);
  });
});
