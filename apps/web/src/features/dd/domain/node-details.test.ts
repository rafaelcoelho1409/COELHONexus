import { describe, expect, it } from "vitest";

import { PLANNER_FINISHED_VALUES } from "../test-fixtures";
import { nodeDetails, nodeKpi, nodeMetrics, nodeRawIO } from "./node-details";
import { PLANNER_NODES, SYNTH_NODES } from "./stages";

describe("node details", () => {
  it("describes every node of both stages, and reads and writes only real state keys", () => {
    for (const node of PLANNER_NODES) {
      const details = nodeDetails("planner", node.id);
      expect(details, node.id).not.toBeNull();
      expect(details?.outputs, node.id).toContain(node.field);
      expect(details?.outputs, node.id).toContain(node.stats);
    }
    for (const node of SYNTH_NODES) {
      const details = nodeDetails("synth", node.id);
      expect(details, node.id).not.toBeNull();
      expect(details?.outputs, node.id).toContain(node.field);
    }
    expect(nodeDetails("planner", "mystery")).toBeNull();
  });

  it("never names the model-routing layer or any provider", () => {
    const text = JSON.stringify([
      ...PLANNER_NODES.map((n) => nodeDetails("planner", n.id)),
      ...SYNTH_NODES.map((n) => nodeDetails("synth", n.id)),
    ]).toLowerCase();
    for (const forbidden of ["rotator", "bandit", "deployment", "litellm", "provider"]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("reads three headline numbers from a finished planner checkpoint", () => {
    expect(nodeMetrics("planner", "corpus_load", PLANNER_FINISHED_VALUES)).toEqual([
      { label: "files", value: "12", note: undefined },
      { label: "bytes", value: "20.0 KB", note: undefined },
      { label: "LLM calls", value: "0", note: "no model involved" },
    ]);
    expect(nodeMetrics("planner", "off_topic", PLANNER_FINISHED_VALUES).map((m) => m.value)).toEqual([
      "12",
      "10/2",
      "0",
    ]);
    // The card line is "value label": it must not repeat the word the label already carries.
    expect(nodeKpi("planner", "off_topic", PLANNER_FINISHED_VALUES)).toBe("12 LLM calls");
    expect(nodeMetrics("planner", "chapter_select", PLANNER_FINISHED_VALUES)[1]?.value).toBe("90%");
  });

  it("says 'pending' instead of inventing zeros while a node has not reported", () => {
    expect(nodeMetrics("planner", "off_topic", {})[0]?.value).toBe("pending");
    expect(nodeMetrics("planner", "chapter_propose", {})[1]?.value).toBe("pending");
    expect(nodeMetrics("synth", "mgsr_replan", {})[0]?.value).toBe("pending");
  });

  it("gives a card its first number as one line", () => {
    expect(nodeKpi("planner", "corpus_load", PLANNER_FINISHED_VALUES)).toBe("12 files");
    expect(nodeKpi("planner", "mystery", {})).toBeNull();
  });

  it("shows the raw state a node reads and writes, and nothing for state that is not there yet", () => {
    const io = nodeRawIO("planner", "off_topic", PLANNER_FINISHED_VALUES);
    expect(io.inputs).toContain('"raw_files"');
    expect(io.outputs).toContain('"off_topic_stats"');
    expect(io.outputs).not.toContain('"raw_files"');
    expect(nodeRawIO("planner", "off_topic", {})).toEqual({ inputs: null, outputs: null });
    expect(nodeRawIO("planner", "mystery", PLANNER_FINISHED_VALUES)).toEqual({ inputs: null, outputs: null });
  });

  it("truncates a very large raw value", () => {
    const io = nodeRawIO("planner", "corpus_load", { raw_files: ["x".repeat(30_000)] });
    expect(io.outputs?.endsWith("… truncated")).toBe(true);
    expect(io.outputs?.length).toBeLessThan(20_100);
  });
});
