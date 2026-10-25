"use client";

/** The row of node cards for one stage, with the drawer of whichever node the reader opened. */

import { useState } from "react";

import { nodeKpi } from "../domain/node-details";
import type { StageNodeView } from "../domain/stages";
import type { StageEvent } from "../entities";
import type { RunStage } from "../params";
import { NodeDrawer } from "./node-drawer";
import { StageNodes } from "./stage-parts";

export function StageNodeBoard({
  stage,
  views,
  values,
  liveLines,
  events,
  threadId,
  live,
}: {
  stage: RunStage;
  views: StageNodeView[];
  values: Record<string, unknown>;
  liveLines: Record<string, string>;
  events: StageEvent[];
  /** The run thread the board shows; its LLM usage fills the drawers. */
  threadId: string | null;
  live: boolean;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const open = views.find((view) => view.node.id === openId);

  const kpis: Record<string, string> = {};
  for (const view of views) {
    const kpi = view.status === "done" ? nodeKpi(stage, view.node.id, values) : null;
    if (kpi) kpis[view.node.id] = kpi;
  }

  return (
    <>
      <StageNodes views={views} liveLines={liveLines} kpis={kpis} onOpen={setOpenId} />
      {open ? (
        <NodeDrawer
          key={open.node.id}
          stage={stage}
          view={open}
          values={values}
          events={events}
          threadId={threadId}
          live={live}
          onClose={() => setOpenId(null)}
        />
      ) : null}
    </>
  );
}
