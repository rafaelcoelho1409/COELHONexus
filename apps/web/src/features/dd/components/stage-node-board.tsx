"use client";

/** One stage's nodes as a graph (default) or as a plain list, with the drawer of whichever node the reader
 * opened. The list is the text equivalent of the graph: the same buttons in order, nothing to pan or zoom. */

import dynamic from "next/dynamic";
import { useState } from "react";

import { nodeKpi } from "../domain/node-details";
import type { StageNodeView } from "../domain/stages";
import type { StageEvent } from "../entities";
import type { RunStage } from "../params";
import { NodeDrawer } from "./node-drawer";
import { StageNodes } from "./stage-parts";

// The graph library is only needed once a graph is on screen.
const StageFlow = dynamic(() => import("./stage-flow").then((module) => module.StageFlow), {
  ssr: false,
  loading: () => <p className="text-xs text-muted-foreground">Loading graph…</p>,
});

const VIEWS = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
] as const;

const STAGE_TITLE: Record<RunStage, string> = { planner: "Planner", synth: "Synth" };

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
  // When each node was last opened, so its Activity tab can mark what arrived since (this page visit only).
  const [openedAt, setOpenedAt] = useState<Record<string, number>>({});
  const [since, setSince] = useState(0);
  const openNode = (nodeId: string) => {
    setSince(openedAt[nodeId] ?? 0);
    setOpenedAt({ ...openedAt, [nodeId]: Date.now() });
    setOpenId(nodeId);
  };
  const [mode, setMode] = useState<(typeof VIEWS)[number]["id"]>("graph");
  const open = views.find((view) => view.node.id === openId);

  const kpis: Record<string, string> = {};
  for (const view of views) {
    const kpi = view.status === "done" ? nodeKpi(stage, view.node.id, values) : null;
    if (kpi) kpis[view.node.id] = kpi;
  }

  return (
    <>
      <div className="flex flex-col gap-2">
        <fieldset className="m-0 flex min-w-0 self-end border-0 p-0">
          <legend className="sr-only">{STAGE_TITLE[stage]} nodes shown as</legend>
          {VIEWS.map((view) => (
            <button
              key={view.id}
              type="button"
              aria-pressed={mode === view.id}
              onClick={() => setMode(view.id)}
              className={`min-h-9 border px-3 text-xs first:rounded-l-sm last:rounded-r-sm ${
                mode === view.id
                  ? "border-primary bg-primary/10 text-primary-text"
                  : "-ml-px border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {view.label}
            </button>
          ))}
        </fieldset>
        {mode === "graph" ? (
          <StageFlow
            stage={stage}
            title={STAGE_TITLE[stage]}
            views={views}
            kpis={kpis}
            liveLines={liveLines}
            values={values}
            live={live}
            onOpen={openNode}
          />
        ) : (
          <StageNodes views={views} liveLines={liveLines} kpis={kpis} onOpen={openNode} />
        )}
      </div>
      {open ? (
        <NodeDrawer
          key={open.node.id}
          stage={stage}
          view={open}
          values={values}
          events={events}
          threadId={threadId}
          live={live}
          since={since}
          onClose={() => setOpenId(null)}
        />
      ) : null}
    </>
  );
}
