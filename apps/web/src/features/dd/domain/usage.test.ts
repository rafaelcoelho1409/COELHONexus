import { describe, expect, it } from "vitest";

import { usagePayloadSchema } from "../entities";
import {
  hasUsage,
  mergeUsage,
  nodeUsageMetrics,
  planHealthView,
  usageFigures,
  usageLine,
  usageRows,
} from "./usage";

const planner = usagePayloadSchema.parse({
  dd_run_id: "planner/alpha/old",
  total: { calls: 14, tokens_in: 9000, tokens_out: 2100, reasoning_tokens: 0 },
  by_node: {
    off_topic: {
      calls: 12,
      tokens_in: 6000,
      tokens_out: 300,
      reasoning_tokens: 0,
      by_model: { "provider/model": { calls: 12 } },
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
    expect(usageLine(planner.total)).toBe("14 calls · 9,000 input tokens · 2,100 output tokens");
    expect(usageFigures(chapter.total).at(-1)).toEqual({ label: "reasoning tokens", value: "200" });
  });

  it("describes one node's usage for its drawer, and nothing for a node that made no call", () => {
    expect(nodeUsageMetrics(planner, "off_topic").map((m) => [m.label, m.value])).toEqual([
      ["LLM calls", "12"],
      ["input tokens", "6,000"],
      ["output tokens", "300"],
    ]);
    expect(nodeUsageMetrics(planner, "corpus_load")).toEqual([]);
    expect(nodeUsageMetrics(null, "off_topic")).toEqual([]);
    // never any model or provider in what is shown
    expect(JSON.stringify(nodeUsageMetrics(planner, "off_topic"))).not.toContain("provider");
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
