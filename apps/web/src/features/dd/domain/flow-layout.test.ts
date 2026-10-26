import { describe, expect, it } from "vitest";

import { flowEdges, flowLayout, loopIteration, NODE_HEIGHT } from "./flow-layout";
import { PLANNER_NODES, SYNTH_NODES } from "./stages";

const ids = (nodes: readonly { id: string }[]) => nodes.map((node) => node.id);

describe("flowLayout", () => {
  it("stacks the planner in one column, in order, with no loop", () => {
    const layout = flowLayout("planner", ids(PLANNER_NODES));
    const ys = ids(PLANNER_NODES).map((id) => layout.positions[id]?.y ?? Number.NaN);
    expect(ys).toEqual([...ys].sort((a, b) => a - b));
    expect(new Set(ys).size).toBe(PLANNER_NODES.length);
    expect(new Set(ids(PLANNER_NODES).map((id) => layout.positions[id]?.x)).size).toBe(1);
    expect(layout.loop).toBeNull();
  });

  it("finds synth's refine loop and leaves room for its arc", () => {
    const planner = flowLayout("planner", ids(PLANNER_NODES));
    const synth = flowLayout("synth", ids(SYNTH_NODES));
    expect(synth.loop).toEqual({ from: "mgsr_replan", to: "sawc_write" });
    expect(synth.width).toBeGreaterThan(planner.width);
  });

  it("drops the loop when either end is not in the graph", () => {
    expect(flowLayout("synth", ["outline_sdp", "sawc_write"]).loop).toBeNull();
  });

  it("is as tall as its nodes and gaps, and is deterministic", () => {
    const nodes = ids(SYNTH_NODES);
    const layout = flowLayout("synth", nodes);
    expect(layout.height).toBeGreaterThan(nodes.length * NODE_HEIGHT);
    expect(flowLayout("synth", nodes)).toEqual(layout);
  });
});

describe("flowEdges", () => {
  it("chains consecutive nodes, then adds the loop edge last", () => {
    const layout = flowLayout("synth", ids(SYNTH_NODES));
    const edges = flowEdges(ids(SYNTH_NODES), layout.loop);
    expect(edges).toHaveLength(SYNTH_NODES.length); // 6 forward edges + the loop
    expect(edges.filter((edge) => edge.kind === "next")).toHaveLength(SYNTH_NODES.length - 1);
    expect(edges.at(-1)).toEqual({
      id: "mgsr_replan->sawc_write",
      source: "mgsr_replan",
      target: "sawc_write",
      kind: "loop",
    });
  });

  it("has no edges for a single node", () => {
    expect(flowEdges(["a"], null)).toEqual([]);
  });
});

describe("loopIteration", () => {
  it("reads the refine counter and ignores anything else", () => {
    expect(loopIteration({ refine_iter: 3 })).toBe(3);
    expect(loopIteration({ refine_iter: "3" })).toBe(0);
    expect(loopIteration({})).toBe(0);
  });
});
