/** Where each node of a stage graph sits. Both graphs are small and fixed (the planner is a line, synth a line
 * with one cycle), so positions are computed here from the node order instead of by a layout engine: a node
 * never moves when its status changes or a stream event lands, and no layout library ships to the browser. */

import type { RunStage } from "../params";

export const NODE_WIDTH = 216;
export const NODE_HEIGHT = 68;
const NODE_GAP = 30;
const PADDING = 16;
/** Room kept to the right of the column for the cycle's arc and its label. */
const LOOP_BULGE = 80;
/** How far the arc itself reaches out from the node edge (the label sits at its far side). */
export const LOOP_REACH = 52;
/** A strip under the last node for React Flow's small credit line, so it never covers a node. */
const CREDIT_ROOM = 32;

type FlowPoint = { x: number; y: number };

export type FlowLayout = {
  positions: Record<string, FlowPoint>;
  width: number;
  height: number;
  /** The conditional edge that sends the run back (synth's refine loop), when both its ends exist. */
  loop: { from: string; to: string } | null;
};

/** The refine cycle of the synth graph: after `mgsr_replan` the run may re-enter `sawc_write`. */
const LOOPS: Record<RunStage, { from: string; to: string } | null> = {
  planner: null,
  synth: { from: "mgsr_replan", to: "sawc_write" },
};

/** One vertical column in node order, with room on the right for the loop arc when the stage has one. */
export function flowLayout(stage: RunStage, ids: readonly string[]): FlowLayout {
  const positions: Record<string, FlowPoint> = {};
  ids.forEach((id, index) => {
    positions[id] = { x: PADDING, y: PADDING + index * (NODE_HEIGHT + NODE_GAP) };
  });
  const wanted = LOOPS[stage];
  const loop = wanted && ids.includes(wanted.from) && ids.includes(wanted.to) ? wanted : null;
  const rows = ids.length;
  return {
    positions,
    width: PADDING * 2 + NODE_WIDTH + (loop ? LOOP_BULGE : 0),
    height: PADDING * 2 + rows * NODE_HEIGHT + Math.max(0, rows - 1) * NODE_GAP + CREDIT_ROOM,
    loop,
  };
}

export type FlowEdgeSpec = { id: string; source: string; target: string; kind: "next" | "loop" };

/** The edges in order, plus the loop edge last. */
export function flowEdges(ids: readonly string[], loop: FlowLayout["loop"]): FlowEdgeSpec[] {
  const edges: FlowEdgeSpec[] = [];
  for (let index = 0; index < ids.length - 1; index += 1) {
    const source = ids[index];
    const target = ids[index + 1];
    if (source && target) edges.push({ id: `${source}->${target}`, source, target, kind: "next" });
  }
  if (loop) edges.push({ id: `${loop.from}->${loop.to}`, source: loop.from, target: loop.to, kind: "loop" });
  return edges;
}

/** The refine loop is "firing" while a chapter is being written, after its first pass: `refine_iter` > 1. */
export function loopIteration(values: Record<string, unknown>): number {
  const iter = values.refine_iter;
  return typeof iter === "number" && Number.isFinite(iter) ? iter : 0;
}
