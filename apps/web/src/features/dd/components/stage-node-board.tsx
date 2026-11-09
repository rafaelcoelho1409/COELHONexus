"use client";

/** One stage's live nodes as a graph or list, or the compiled LangGraph topology, with the drawer of whichever node the reader opened. */

import dynamic from "next/dynamic";
import { useState } from "react";

import { nodeKpi } from "../domain/node-details";
import type { StageNodeView } from "../domain/stages";
import type { StageEvent } from "../entities";
import { useStageInfo } from "../hooks/use-stage-queries";
import type { RunStage } from "../params";
import { LangGraphTopology } from "./langgraph-topology";
import { NodeDrawer } from "./node-drawer";
import { StageNodes } from "./stage-parts";
import { ToggleGroup } from "./toggle-group";

// The graph library is only needed once a graph is on screen.
const StageFlow = dynamic(() => import("./stage-flow").then((module) => module.StageFlow), {
  ssr: false,
  loading: () => <p className="text-xs text-muted-foreground">Loading graph…</p>,
});

const VIEWS = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
  { id: "langgraph", label: "LangGraph" },
] as const;

type ViewMode = (typeof VIEWS)[number]["id"];

const STAGE_TITLE: Record<RunStage, string> = { planner: "Planner", synth: "Synth" };

export function StageNodeBoard({
  stage,
  views,
  values,
  liveLines,
  events,
  threadId,
  live,
  caption,
}: {
  stage: RunStage;
  views: StageNodeView[];
  values: Record<string, unknown>;
  liveLines: Record<string, string>;
  events: StageEvent[];
  /** The run thread the board shows; its LLM usage fills the drawers. */
  threadId: string | null;
  live: boolean;
  /** One line of context for the graph (whose nodes these are, how far the run is), on the left of the view switch. */
  caption: string;
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
  const [mode, setMode] = useState<ViewMode | null>(null);
  const activeMode: ViewMode = mode ?? (views.length > 0 ? "graph" : "langgraph");
  const stageInfo = useStageInfo(stage);
  const open = views.find((view) => view.node.id === openId);

  const kpis: Record<string, string> = {};
  for (const view of views) {
    const kpi = view.status === "done" ? nodeKpi(stage, view.node.id, values) : null;
    if (kpi) kpis[view.node.id] = kpi;
  }

  return (
    <>
      {/* The graph sits in a box like Ingestion's "Agentic pipeline", with the same run views and topology source. */}
      <section
        aria-label={`${STAGE_TITLE[stage]} pipeline`}
        className="flex min-w-0 flex-1 flex-col gap-3 rounded-md border border-border bg-card p-3"
      >
        {/* One row, the same height in both stages (the switch is 36px; the caption takes two lines of 16px at most), so
            the planner's graph and the synth graph start at the same height. */}
        <div className="flex min-h-9 items-center justify-between gap-3">
          <p
            aria-live="polite"
            title={caption}
            className="line-clamp-2 min-w-0 text-xs leading-4 text-muted-foreground"
          >
            {caption}
          </p>
          <div className="shrink-0">
            <ToggleGroup
              legend={`${STAGE_TITLE[stage]} nodes shown as`}
              options={VIEWS}
              value={activeMode}
              onChange={setMode}
            />
          </div>
        </div>
        {activeMode === "langgraph" ? (
          <LangGraphTopology title={STAGE_TITLE[stage]} source={stageInfo?.graph_mermaid ?? null} />
        ) : views.length === 0 ? (
          <p className="flex min-h-36 items-center justify-center rounded-sm border border-border bg-background p-3 text-center text-xs text-muted-foreground">
            Run {STAGE_TITLE[stage]} to see execution status for its nodes.
          </p>
        ) : activeMode === "graph" ? (
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
      </section>
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
