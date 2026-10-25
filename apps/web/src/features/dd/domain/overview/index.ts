import type { RunStage } from "../../params";
import { PLANNER_OVERVIEWS } from "./planner";
import { SYNTH_OVERVIEWS } from "./synth";
import { emptyOverview, type NodeOverview } from "./types";

export type { NodeOverview, OverviewCell, OverviewSection } from "./types";

/** The drawer's Overview content for one node of the given stage. */
export function buildOverview(
  stage: RunStage,
  nodeId: string,
  values: Record<string, unknown>,
): NodeOverview {
  const builder = (stage === "planner" ? PLANNER_OVERVIEWS : SYNTH_OVERVIEWS)[nodeId];
  return builder ? builder(values) : emptyOverview("This node has no detail view yet.");
}
