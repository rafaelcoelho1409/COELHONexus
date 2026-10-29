import { describe, expect, it } from "vitest";

import { usagePayloadSchema } from "../entities";
import {
  hasUsage,
  mergeMetrics,
  mergeUsage,
  nodeModelRows,
  nodeUsageMetrics,
  nodeUsageTable,
  payloadModelRows,
  pipelineFigures,
  planHealthView,
  usageFigures,
  usageRows,
} from "./usage";

const planner = usagePayloadSchema.parse({
  dd_run_id: "planner/alpha/old",
  total: { calls: 14, tokens_in: 9000, tokens_out: 2100, reasoning_tokens: 0, errors: 3 },
  by_node: {
    off_topic: {
      calls: 12,
      tokens_in: 6000,
      tokens_out: 300,
      reasoning_tokens: 0,
      errors: 2,
      by_model: {
        "vendor-a/model-small": { calls: 9, tokens_in: 4500, tokens_out: 200, reasoning_tokens: 0 },
        "vendor-b/model-large": { calls: 3, tokens_in: 1500, tokens_out: 100, reasoning_tokens: 0 },
      },
    },
    chapter_propose: { calls: 2, tokens_in: 3000, tokens_out: 1800, reasoning_tokens: 0, by_model: {} },
    corpus_load: { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, by_model: {} },
  },
});

const chapter = usagePayloadSchema.parse({
  total: { calls: 5, tokens_in: 4000, tokens_out: 1000, reasoning_tokens: 200 },
  by_node: { sawc_write: { calls: 5, tokens_in: 4000, tokens_out: 1000, reasoning_tokens: 200 } },
});

describe("usage payloads", () => {
  it("tolerates a sparse or damaged payload by counting zero", () => {
    const empty = usagePayloadSchema.parse({});
    expect(empty.total.calls).toBe(0);
    expect(empty.by_node).toEqual({});
    expect(hasUsage(empty)).toBe(false);
    expect(hasUsage(null)).toBe(false);
    expect(hasUsage(planner)).toBe(true);
  });

  it("merges runs, adding totals and per-node counts", () => {
    const merged = mergeUsage([planner, chapter, null, chapter]);
    expect(merged.total).toMatchObject({
      calls: 24,
      tokens_in: 17_000,
      tokens_out: 4_100,
      reasoning_tokens: 400,
    });
    expect(merged.by_node.sawc_write?.calls).toBe(10);
    expect(merged.by_node.off_topic?.calls).toBe(12);
  });

  it("lists busy nodes first, named like the node board, and skips idle ones", () => {
    expect(usageRows("planner", planner).map((row) => [row.label, row.calls])).toEqual([
      ["Off-topic filter", 12],
      ["Chapter propose", 2],
    ]);
    expect(usageRows("synth", chapter)[0]?.label).toBe("SAWC write");
    // an id the node tables do not know keeps its id
    expect(
      usageRows("synth", mergeUsage([usagePayloadSchema.parse({ by_node: { mystery: { calls: 1 } } })]))[0]
        ?.label,
    ).toBe("mystery");
  });

  it("words the figures, and shows reasoning tokens only when there were some", () => {
    expect(usageFigures(planner.total).map((f) => f.label)).toEqual([
      "calls",
      "input tokens",
      "output tokens",
    ]);
    expect(usageFigures(chapter.total).at(-1)).toEqual({ label: "reasoning tokens", value: "200" });
  });

  it("totals the pipeline and splits each figure between the planner and the synth chapters", () => {
    expect(pipelineFigures(planner.total, chapter.total)).toEqual([
      { label: "LLM calls", value: "19", planner: "14", synth: "5" },
      { label: "Input tokens", value: "13,000", planner: "9,000", synth: "4,000" },
      { label: "Output tokens", value: "3,100", planner: "2,100", synth: "1,000" },
      { label: "Reasoning tokens", value: "200", planner: "0", synth: "200" },
    ]);
    // no reasoning tokens anywhere: no such figure
    expect(pipelineFigures(planner.total, planner.total).map((figure) => figure.label)).toEqual([
      "LLM calls",
      "Input tokens",
      "Output tokens",
    ]);
  });

  it("describes one node's usage for its drawer, and nothing for a node that made no call", () => {
    expect(nodeUsageMetrics(planner, "off_topic").map((m) => [m.label, m.value])).toEqual([
      ["LLM calls", "12"],
      ["input tokens", "6,000"],
      ["output tokens", "300"],
      ["top model", "vendor-a/model-small"],
    ]);
    expect(nodeUsageMetrics(planner, "off_topic").at(-1)?.note).toBe("9 calls");
    expect(nodeUsageMetrics(planner, "chapter_propose").map((m) => m.label)).not.toContain("top model"); // none reported
    expect(nodeUsageMetrics(planner, "corpus_load")).toEqual([]);
    expect(nodeUsageMetrics(null, "off_topic")).toEqual([]);
  });

  it("lists a node's models busiest first with their own calls and tokens, the id exactly as reported", () => {
    expect(nodeModelRows(planner, "off_topic")).toMatchObject([
      { model: "vendor-a/model-small", calls: 9, tokens_in: 4500, tokens_out: 200, reasoning_tokens: 0 },
      { model: "vendor-b/model-large", calls: 3, tokens_in: 1500, tokens_out: 100, reasoning_tokens: 0 },
    ]);
    expect(nodeModelRows(planner, "chapter_propose")).toEqual([]);
    expect(nodeModelRows(planner, "unknown_node")).toEqual([]);
    expect(nodeModelRows(null, "off_topic")).toEqual([]);
  });

  it("drops a model that answered nothing, and breaks a tie by name", () => {
    const payload = usagePayloadSchema.parse({
      by_node: {
        a: { by_model: { "m/zeta": { calls: 2 }, "m/alpha": { calls: 2 }, "m/idle": { calls: 0 } } },
      },
    });
    expect(nodeModelRows(payload, "a").map((row) => row.model)).toEqual(["m/alpha", "m/zeta"]);
  });

  it("adds the models up across nodes, and across runs", () => {
    const other = usagePayloadSchema.parse({
      by_node: {
        off_topic: {
          calls: 4,
          by_model: { "vendor-b/model-large": { calls: 4, tokens_in: 400, tokens_out: 40 } },
        },
        doc_distill: {
          calls: 5,
          by_model: { "vendor-a/model-small": { calls: 5, tokens_in: 50, tokens_out: 5 } },
        },
      },
    });
    expect(payloadModelRows(mergeUsage([planner, other]))).toMatchObject([
      { model: "vendor-a/model-small", calls: 14, tokens_in: 4550, tokens_out: 205 },
      { model: "vendor-b/model-large", calls: 7, tokens_in: 1900, tokens_out: 140 },
    ]);
    expect(mergeUsage([planner, other]).by_node.off_topic?.by_model["vendor-b/model-large"]?.calls).toBe(7);
    expect(payloadModelRows(null)).toEqual([]);
  });

  it("counts failed calls, and adds them across runs", () => {
    expect(planner.total.errors).toBe(3);
    expect(planner.by_node.off_topic?.errors).toBe(2);
    expect(usagePayloadSchema.parse({ total: { calls: 1 } }).total.errors).toBe(0); // older runs carry none
    expect(mergeUsage([planner, planner]).total.errors).toBe(6);
    expect(mergeUsage([planner, planner]).by_node.off_topic?.errors).toBe(4);
  });

  it("keeps a figure once when the usage and the node both report it, the exact usage first", () => {
    const usage = [{ label: "LLM calls", value: "12", note: "exact usage" }];
    const own = [
      { label: "llm calls", value: "11" },
      { label: "kept/dropped", value: "9/2" },
    ];
    expect(mergeMetrics(usage, own)).toEqual([...usage, { label: "kept/dropped", value: "9/2" }]);
    expect(mergeMetrics([], own)).toEqual(own);
  });
});

describe("a node's LLM calls and tokens table", () => {
  it("sets the node beside the whole run, with its share and what each call cost on average", () => {
    const table = nodeUsageTable("planner", planner, "off_topic");
    expect(table?.runLabel).toBe("Whole run");
    expect(table?.rows.map((row) => [row.label, row.node, row.run, row.share])).toEqual([
      ["Calls", "12", "14", "86%"],
      ["Failed calls", "2", "3", "67%"],
      ["Input tokens", "6,000", "9,000", "67%"],
      ["Output tokens", "300", "2,100", "14%"],
      ["Input per call", "500", "643", "—"],
      ["Output per call", "25", "150", "—"],
    ]);
  });

  it("shows failed and reasoning rows only when the run had any, and names a synth run a chapter", () => {
    const table = nodeUsageTable("synth", chapter, "sawc_write");
    expect(table?.runLabel).toBe("Whole chapter");
    expect(table?.rows.map((row) => row.label)).toEqual([
      "Calls",
      "Input tokens",
      "Output tokens",
      "Reasoning tokens",
      "Input per call",
      "Output per call",
    ]);
  });

  it("has no table for a node that made no call, or before the counters load", () => {
    expect(nodeUsageTable("planner", planner, "corpus_load")).toBeNull();
    expect(nodeUsageTable("planner", planner, "unknown_node")).toBeNull();
    expect(nodeUsageTable("planner", null, "off_topic")).toBeNull();
  });

  it("says a tiny share is under one percent, and never divides by an empty run", () => {
    const payload = usagePayloadSchema.parse({
      total: { calls: 1_000, tokens_in: 1_000_000 },
      by_node: { a: { calls: 1, tokens_in: 1 }, b: { calls: 999, tokens_in: 999_999 } },
    });
    expect(nodeUsageTable("planner", payload, "a")?.rows[0]?.share).toBe("<1%");
    const empty = usagePayloadSchema.parse({ total: {}, by_node: { a: { calls: 2 } } });
    expect(nodeUsageTable("planner", empty, "a")?.rows[0]?.share).toBe("—");
  });
});

describe("plan health", () => {
  it("is unknown when the server could not say", () => {
    expect(planHealthView(null, {})).toEqual({ status: "unknown", reasons: [] });
  });

  it("is healthy without reasons when nothing fell back", () => {
    expect(planHealthView(false, { doc_distill_fallback_pct: 3, chapter_select_n_chapters: 8 })).toEqual({
      status: "healthy",
      reasons: [],
    });
  });

  it("says in words what degraded the plan", () => {
    const view = planHealthView(true, {
      chapter_propose_fallback_used: true,
      doc_distill_fallback_pct: 55.5,
      chapter_assign_fallback_pct: 60,
      chapter_select_n_chapters: 2,
      oversized_chapter: true,
      chapter_largest_share_pct: 31.2,
      chapter_select_unplaced_docs: 4,
      off_topic_llm_errors: 2,
    });
    expect(view.status).toBe("degraded");
    expect(view.reasons).toEqual([
      "Chapter proposals fell back to deterministic titles.",
      "55.5% of documents used the distill fallback.",
      "60% of documents used the lexical assignment fallback.",
      "Only 2 chapters survived selection.",
      "One chapter holds 31.2% of all sources.",
      "4 documents were lost between assignment and the plan.",
      "2 off-topic judge calls failed.",
    ]);
  });
});
