"use client";

/** Planner stage: run bar, node board (each node opens its drawer), plan health and LLM usage. */

import { useState } from "react";

import { type CrossStageBlocker, useCrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import { type PlannerRunController, usePlannerRun } from "../hooks/use-planner-run";
import { PlanHealth } from "./plan-health";
import { StageNodeBoard } from "./stage-node-board";
import { NoFrameworkCard, RunBar } from "./stage-parts";
import { PlannerUsageSheet } from "./usage-sheet";

/** The planner screen without its data source, so the pipeline page can drive it from its own controller. */
export function PlannerBoard({
  slug,
  planner,
  blocker,
}: {
  slug: string;
  planner: PlannerRunController;
  blocker: CrossStageBlocker;
}) {
  const [usageOpen, setUsageOpen] = useState(false);
  return (
    <div className="flex flex-col gap-4">
      <RunBar
        title="Planner"
        testId="planner"
        controls={planner}
        blockedMessage={blocker.message}
        onOpenUsage={() => setUsageOpen(true)}
      />
      {"plan_path" in planner.values && !planner.running ? <PlanHealth slug={slug} /> : null}
      {planner.views.length > 0 ? (
        <StageNodeBoard
          stage="planner"
          views={planner.views}
          values={planner.values}
          liveLines={planner.liveLines}
          events={planner.events}
          threadId={planner.threadId}
          live={planner.running}
        />
      ) : null}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{planner.progress ? `Progress: ${planner.progress}` : ""}</span>
        <span className="hidden sm:inline">
          {planner.next.length > 0 ? `Next: ${planner.next.join(", ")}` : ""}
        </span>
      </div>
      {planner.error ? (
        <p role="alert" className="text-xs text-destructive-text">
          {planner.error}
        </p>
      ) : null}
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
