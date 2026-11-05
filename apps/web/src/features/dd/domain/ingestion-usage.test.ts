import { describe, expect, it } from "vitest";

import { usagePayloadSchema } from "../entities";
import { ingestionUsageRows, nodeUsageForIngestionStep } from "./ingestion-usage";

const USAGE = usagePayloadSchema.parse({
  total: { calls: 10, tokens_in: 1000, tokens_out: 400, reasoning_tokens: 20, errors: 1 },
  by_node: {
    page_auditor: {
      calls: 6,
      tokens_in: 600,
      tokens_out: 100,
      reasoning_tokens: 0,
      errors: 0,
      by_model: {
        "vendor-a/model-x": { calls: 4, tokens_in: 400, tokens_out: 60, reasoning_tokens: 0 },
        "vendor-b/model-y": { calls: 2, tokens_in: 200, tokens_out: 40, reasoning_tokens: 0 },
      },
    },
    page_repair: {
      calls: 4,
      tokens_in: 400,
      tokens_out: 300,
      reasoning_tokens: 20,
      errors: 1,
      by_model: {
        "vendor-a/model-x": { calls: 4, tokens_in: 400, tokens_out: 300, reasoning_tokens: 20 },
      },
    },
  },
});

describe("ingestionUsageRows", () => {
  it("groups by agent, busiest first, under the agent's name", () => {
    const rows = ingestionUsageRows(USAGE, "agent");
    expect(rows.map((row) => [row.agent, row.calls])).toEqual([
      ["Page auditor", 6],
      ["Page repair", 4],
    ]);
  });

  it("groups by model, adding an agent's models together, and splits the provider off", () => {
    const rows = ingestionUsageRows(USAGE, "model");
    expect(rows.map((row) => [row.provider, row.model, row.calls])).toEqual([
      ["vendor-a", "model-x", 8],
      ["vendor-b", "model-y", 2],
    ]);
  });

  it("groups by agent and model", () => {
    const rows = ingestionUsageRows(USAGE, "agent_model");
    // Equal calls and input tokens: the one that wrote more output comes first.
    expect(rows.map((row) => `${row.agent}:${row.model}`)).toEqual([
      "Page repair:model-x",
      "Page auditor:model-x",
      "Page auditor:model-y",
    ]);
  });

  it("keeps to the agents a node runs", () => {
    expect(ingestionUsageRows(USAGE, "agent", "normalize_pages").map((row) => row.agent)).toEqual([
      "Page repair",
    ]);
    expect(ingestionUsageRows(USAGE, "agent", "publish")).toEqual([]);
    expect(ingestionUsageRows(undefined, "model")).toEqual([]);
  });
});

describe("nodeUsageForIngestionStep", () => {
  it("is the node's agent counters, or null when none was counted", () => {
    expect(nodeUsageForIngestionStep(USAGE, "audit_pages")).toMatchObject({ calls: 6, tokens_in: 600 });
    expect(nodeUsageForIngestionStep(USAGE, "normalize_pages")?.by_model["vendor-a/model-x"]?.calls).toBe(4);
    expect(nodeUsageForIngestionStep(USAGE, "publish")).toBeNull();
    expect(nodeUsageForIngestionStep(undefined, "audit_pages")).toBeNull();
  });
});
