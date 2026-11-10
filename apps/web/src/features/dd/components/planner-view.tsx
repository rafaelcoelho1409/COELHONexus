"use client";

/** Planner stage: run bar, node board (each node opens its drawer), plan health and LLM usage. */

import { useState } from "react";

import { stageCaption } from "../domain/stages";
import { type CrossStageBlocker, useCrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import { type PlannerRunController, usePlannerRun } from "../hooks/use-planner-run";
import { PlanHealth } from "./plan-health";
import { StageNodeBoard } from "./stage-node-board";
import { NoFrameworkCard, PipelineStageProgress, RunBar } from "./stage-parts";
import { PlannerUsageSheet } from "./usage-sheet";

/** Planner's standalone tab: its run bar and graph share one card. The Pipeline tab splits those into progress and graph panels. */
export function PlannerBoard({
  slug,
  planner,
  blocker,
  className = "",
}: {
  slug: string;
  planner: PlannerRunController;
  blocker: CrossStageBlocker;
  className?: string;
}) {
  const [usageOpen, setUsageOpen] = useState(false);
  return (
    <>
      <section
        aria-label="Planner Agentic AI"
        className={`flex min-w-0 flex-col gap-4 rounded-md border border-border bg-card p-3 ${className}`}
      >
        <RunBar
          title="Planner"
          testId="planner"
          controls={planner}
          blockedMessage={blocker.message}
          onOpenUsage={() => setUsageOpen(true)}
          embedded
        >
          {"plan_path" in planner.values && !planner.running ? <PlanHealth slug={slug} /> : null}
        </RunBar>
        <div className="flex min-w-0 flex-col gap-4">
          <StageNodeBoard
            stage="planner"
            views={planner.views}
            values={planner.values}
            liveLines={planner.liveLines}
            events={planner.events}
            threadId={planner.threadId}
            live={planner.running}
            embedded
            caption={
              planner.views.length > 0
                ? stageCaption(planner.views, planner.progress, planner.next)
                : "Compiled Planner topology · run Planner to see execution status."
            }
          />
          {planner.error ? (
            <p role="alert" className="text-xs text-destructive-text">
              {planner.error}
            </p>
          ) : null}
        </div>
      </section>
      {usageOpen ? (
        <PlannerUsageSheet
          threadId={planner.threadId}
          live={planner.running}
          onClose={() => setUsageOpen(false)}
        />
      ) : null}
    </>
  );
}

/** Pipeline's first-row progress card, separated from the graph column below it. */
export function PlannerProgressPanel({
  slug,
  planner,
  blocker,
}: {
  slug: string;
  planner: PlannerRunController;
  blocker: CrossStageBlocker;
}) {
  const [usageOpen, setUsageOpen] = useState(false);
  const activeViews = planner.views.filter((view) => view.status !== "future");
  const completed = activeViews.filter((view) => view.status === "done").length;
  const running = activeViews.filter((view) => view.status === "running").length;
  const total = activeViews.length;
  const current = Math.min(total, completed + running);
  const summary = total
    ? `${completed} of ${total} nodes complete${running ? ` · ${running} in progress` : ""}`
    : "Planner node progress is unavailable.";
  const valueText = total
    ? `${current} of ${total} Planner nodes reached`
    : "Planner node progress is unavailable";

  return (
    <>
      <PipelineStageProgress
        title="Planner"
        testId="planner"
        controls={planner}
        blockedMessage={blocker.message}
        error={planner.error}
        onOpenUsage={() => setUsageOpen(true)}
        current={current}
        total={total}
        valueText={valueText}
        summary={summary}
        indeterminate={planner.running}
      >
        {"plan_path" in planner.values && !planner.running ? <PlanHealth slug={slug} /> : null}
      </PipelineStageProgress>
      {usageOpen ? (
        <PlannerUsageSheet
          threadId={planner.threadId}
          live={planner.running}
          onClose={() => setUsageOpen(false)}
        />
      ) : null}
    </>
  );
}

/** Planner's lower-row graph box for the shared Pipeline layout. */
export function PlannerGraphPanel({ planner }: { planner: PlannerRunController }) {
  return (
    <section
      aria-label="Planner Agentic AI graph"
      className="flex min-w-0 flex-col gap-3 rounded-md border border-border bg-card p-3"
    >
      <h3 className="text-sm font-medium">Planner graph</h3>
      <StageNodeBoard
        stage="planner"
        views={planner.views}
        values={planner.values}
        liveLines={planner.liveLines}
        events={planner.events}
        threadId={planner.threadId}
        live={planner.running}
        embedded
        caption={
          planner.views.length > 0
            ? stageCaption(planner.views, planner.progress, planner.next)
            : "Compiled Planner topology · run Planner to see execution status."
        }
      />
    </section>
  );
}

function PlannerStage({ slug }: { slug: string }) {
  const blocker = useCrossStageBlocker("planner");
  const planner = usePlannerRun(slug);
  return <PlannerBoard slug={slug} planner={planner} blocker={blocker} />;
}

export function PlannerView({ slug }: { slug: string | null }) {
  // `key`: a different framework is a fresh controller, so no state leaks between frameworks.
  return slug ? <PlannerStage key={slug} slug={slug} /> : <NoFrameworkCard />;
}
