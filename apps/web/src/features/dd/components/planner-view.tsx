"use client";

/** Planner stage: run bar, node board (each node opens its drawer), plan health and LLM usage. */

import { useState } from "react";

import { stageCaption } from "../domain/stages";
import { type CrossStageBlocker, useCrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import { type PlannerRunController, usePlannerRun } from "../hooks/use-planner-run";
import { PlanHealth } from "./plan-health";
import { StageNodeBoard } from "./stage-node-board";
import { NoFrameworkCard, RunBar } from "./stage-parts";
import { PlannerUsageSheet } from "./usage-sheet";

/** The planner screen without its data source, so the pipeline page can drive it from its own controller. Two rows —
 * the run bar, then everything under it — so the pipeline page can lay it on shared grid rows (`className`). */
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
    <div className={`flex min-w-0 flex-col gap-4 ${className}`}>
      <RunBar
        title="Planner"
        testId="planner"
        controls={planner}
        blockedMessage={blocker.message}
        onOpenUsage={() => setUsageOpen(true)}
      >
        {"plan_path" in planner.values && !planner.running ? <PlanHealth slug={slug} /> : null}
      </RunBar>
      <div className="flex min-w-0 flex-col gap-4">
        {planner.views.length > 0 ? (
          <StageNodeBoard
            stage="planner"
            views={planner.views}
            values={planner.values}
            liveLines={planner.liveLines}
            events={planner.events}
            threadId={planner.threadId}
            live={planner.running}
            caption={stageCaption(planner.views, planner.progress, planner.next)}
          />
        ) : null}
        {planner.error ? (
          <p role="alert" className="text-xs text-destructive-text">
            {planner.error}
          </p>
        ) : null}
      </div>
      {usageOpen ? (
        <PlannerUsageSheet
          threadId={planner.threadId}
          live={planner.running}
          onClose={() => setUsageOpen(false)}
        />
      ) : null}
    </div>
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
