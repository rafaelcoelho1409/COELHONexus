"use client";

/** The pipeline stage: planner and synth on one page, with the opt-in "Auto-chain" that starts synth as
 * soon as the planner finishes cleanly. The two stages never run at the same time. */

import { proposedTitles } from "../domain/chapters";
import { useAutoChain } from "../hooks/use-auto-chain";
import { useCrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import { useDdOverlays } from "../hooks/use-overlays";
import { usePlannerRun } from "../hooks/use-planner-run";
import { useSynthRun } from "../hooks/use-synth-run";
import { ChaptersColumn } from "./chapters-column";
import { PipelineTotal } from "./pipeline-total";
import { PlannerBoard } from "./planner-view";
import { NoFrameworkCard } from "./stage-parts";
import { SynthRunPanel } from "./synth-view";

function PipelineWorkbench({ slug }: { slug: string }) {
  const overlays = useDdOverlays();
  const plannerBlocker = useCrossStageBlocker("planner");
  const synthBlocker = useCrossStageBlocker("synth");
  const synth = useSynthRun(slug);
  const chain = useAutoChain(synth.start, (message) => overlays.notice(message, "error"));
  const planner = usePlannerRun(slug, { onTerminal: (status) => void chain.onPlannerFinished(status) });

  return (
    <div className="flex flex-col gap-6" data-testid="pipeline-workbench">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <p className="max-w-prose text-xs text-muted-foreground">
          Plan first, then chapters are synthesized in order and harmonized. The two stages never run
          concurrently. Open any node for its inputs, outputs and LLM usage.
        </p>
        <label
          htmlFor="dd-auto-chain"
          className="flex min-h-9 items-center gap-2 text-sm"
          title="When the planner finishes successfully, synth starts by itself. Off by default."
        >
          <input
            id="dd-auto-chain"
            type="checkbox"
            checked={chain.enabled}
            onChange={(event) => chain.setEnabled(event.target.checked)}
            className="size-4 accent-primary"
          />
          Auto-chain
        </label>
      </div>
      <PipelineTotal planner={planner} synth={synth} />
      {/* Three columns, left to right as the work flows: the planner's graph, the synth graph of one chapter, and the chapters
          the planner proposes (the original's chapter strip). The chapters stay a column from 1024px up (a narrower strip
          below 1280px); between 768 and 1023px they take a row under the two graphs, and on a phone everything stacks. */}
      <div
        data-testid="pipeline-columns"
        className="grid grid-cols-1 items-start gap-6 md:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,16rem)] xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,20rem)]"
      >
        <PlannerBoard slug={slug} planner={planner} blocker={plannerBlocker} />
        <SynthRunPanel synth={synth} blocker={synthBlocker} />
        <div className="md:col-span-2 lg:col-span-1">
          <ChaptersColumn
            slug={slug}
            synth={synth}
            blocker={synthBlocker}
            proposed={proposedTitles(planner.values)}
            planning={planner.running}
          />
        </div>
      </div>
    </div>
  );
}

export function PipelineView({ slug }: { slug: string | null }) {
  // `key`: a different framework is a fresh pair of controllers.
  return slug ? <PipelineWorkbench key={slug} slug={slug} /> : <NoFrameworkCard />;
}
