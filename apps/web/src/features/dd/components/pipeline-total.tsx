"use client";

/** The whole pipeline's LLM usage in one strip — shown only once the plan is written and every chapter is
 * rendered, so the figure is final rather than a moving partial. */

import { hasUsage, mergeUsage, usageLine } from "../domain/usage";
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
  const total = mergeUsage([plannerUsage, ...chapters.map((entry) => entry.usage)]);

  if (!final || !hasUsage(total)) return null;
  return (
    <p className="text-xs text-muted-foreground">
      <span className="font-medium text-foreground">Pipeline total</span> · {usageLine(total.total)}
    </p>
  );
}
