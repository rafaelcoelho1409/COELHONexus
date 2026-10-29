"use client";

/** The whole pipeline's LLM usage as a strip of figures — shown only once the plan is written and every chapter is
 * rendered, so the figures are final rather than moving partials. Each figure says what the planner and the synth
 * chapters each used of it. */

import { hasUsage, mergeUsage, pipelineFigures } from "../domain/usage";
import type { PlannerRunController } from "../hooks/use-planner-run";
import type { SynthRunController } from "../hooks/use-synth-run";
import { useChapterUsage, useRunUsage } from "../hooks/use-usage";

export function PipelineTotal({
  planner,
  synth,
}: {
  planner: PlannerRunController;
  synth: SynthRunController;
}) {
  const planDone = "plan_path" in planner.values && !planner.running;
  const bookDone =
    synth.cells.length > 0 && synth.cells.every((cell) => cell.status === "done") && !synth.running;
  const final = planDone && bookDone;

  const plannerUsage = useRunUsage("planner", planner.threadId, { live: false, enabled: final });
  const chapters = useChapterUsage(synth.cells, { live: false, enabled: final });
  const synthUsage = mergeUsage(chapters.map((entry) => entry.usage));
  const total = mergeUsage([plannerUsage, synthUsage]);
  // Each stage's usage loads on its own; summing before the last one arrives would print a smaller number that then grows.
  const loaded = plannerUsage !== null && chapters.every((entry) => entry.usage !== null);

  if (!final || !loaded || !hasUsage(total)) return null;
  return (
    <section
      aria-label="Pipeline total"
      className="flex flex-col gap-3 rounded-md border border-border bg-card p-3 lg:flex-row lg:items-center lg:gap-6"
    >
      <div className="shrink-0">
        <h3 className="text-sm font-medium">Pipeline total</h3>
        <p className="text-xs text-muted-foreground">The plan plus every chapter</p>
      </div>
      <dl className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]">
        {pipelineFigures(plannerUsage.total, synthUsage.total).map((figure) => (
          <div key={figure.label} className="min-w-0 rounded-sm border border-border px-3 py-2">
            <dt className="text-xs text-muted-foreground">{figure.label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{figure.value}</dd>
            <dd className="flex flex-wrap gap-x-3 text-xs text-muted-foreground tabular-nums">
              <span>Planner {figure.planner}</span>
              <span>Synth {figure.synth}</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
