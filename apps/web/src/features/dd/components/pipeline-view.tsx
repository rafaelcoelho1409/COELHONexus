"use client";

/** The pipeline stage: planner and synth on one page, with the opt-in "Auto-chain" (chosen in the third navbar) that
 * starts synth as soon as the planner finishes cleanly. The two stages never run at the same time. */

import { proposedTitles } from "../domain/chapters";
import { useAutoChain } from "../hooks/use-auto-chain";
import { useCrossStageBlocker } from "../hooks/use-cross-stage-blocker";
import { useDdOverlays } from "../hooks/use-overlays";
import { usePlannerRun } from "../hooks/use-planner-run";
import { useSynthRun } from "../hooks/use-synth-run";
import { ChaptersColumn } from "./chapters-column";
import { PipelineTotal } from "./pipeline-total";
import { PlannerGraphPanel, PlannerProgressPanel } from "./planner-view";
import { NoFrameworkCard } from "./stage-parts";
import { SynthGraphPanel, SynthProgressPanel } from "./synth-view";

function PipelineWorkbench({ slug }: { slug: string }) {
  const overlays = useDdOverlays();
  const plannerBlocker = useCrossStageBlocker("planner");
  const synthBlocker = useCrossStageBlocker("synth");
  const synth = useSynthRun(slug);
  const chain = useAutoChain(synth.start, (message) => overlays.notice(message, "error"));
  const planner = usePlannerRun(slug, {
    onTerminal: (status) => {
      const continueChain = () => chain.onPlannerFinished(status);
      // A durable plan may exist for any terminal status. Refresh every time so Chapters gets final titles/actions
      // without requiring a page reload; auto-chain still only proceeds on "done".
      void synth.refreshPlan().then(continueChain, continueChain);
    },
    onWipe: synth.clearPlan,
  });

  return (
    <div className="flex flex-col gap-6" data-testid="pipeline-workbench">
      <PipelineTotal planner={planner} synth={synth} />
      {/* Each process gets an Ingestion-style progress card above the three-column workbench. */}
      <div
        data-testid="pipeline-progress-row"
        className="grid grid-cols-1 items-stretch gap-3 md:grid-cols-2"
      >
        <PlannerProgressPanel slug={slug} planner={planner} blocker={plannerBlocker} />
        <SynthProgressPanel synth={synth} blocker={synthBlocker} />
      </div>
      {/* The lower row keeps the Planner graph, Synth graph, and chapters column in workflow order. */}
      <div
        data-testid="pipeline-columns"
        className="grid grid-cols-1 items-stretch gap-x-6 gap-y-4 md:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,16rem)] xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,20rem)]"
      >
        <PlannerGraphPanel planner={planner} />
        <SynthGraphPanel synth={synth} />
        <div className="md:col-span-2 lg:relative lg:col-span-1 lg:col-start-3 lg:min-h-160 lg:self-stretch">
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
