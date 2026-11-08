"use client";

/** How the latest plan was produced, under the title row of the planner's run box (a flex-wrap row): one short
 * "Plan health: healthy" item, and — only when it ran on deterministic fallbacks — a full-width line saying so with
 * the reasons under it. */

import { planHealthView } from "../domain/usage";
import { usePlannerHealth } from "../hooks/use-usage";

const TONE = { healthy: "text-success-text", degraded: "text-warning-text" } as const;

export function PlanHealth({ slug }: { slug: string }) {
  const health = usePlannerHealth(slug, true);
  const view = health ? planHealthView(health.degraded, health.pipeline_health) : null;
  const chapters = health?.chapters ?? [];
  if ((!view || view.status === "unknown") && chapters.length === 0) return null;

  return (
    <>
      {view && view.status !== "unknown" ? (
        <p className="text-xs">
          <span className="text-muted-foreground">Plan health: </span>
          <span className={`font-medium ${TONE[view.status]}`}>{view.status}</span>
        </p>
      ) : null}
      {view && (view.status === "degraded" || view.reasons.length > 0) ? (
        <div className="basis-full text-xs text-muted-foreground">
          {view.status === "degraded" ? <p>It ran on fallbacks, so review it before synthesizing.</p> : null}
          {view.reasons.length > 0 ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {view.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {chapters.length > 0 ? (
        <details className="basis-full text-xs">
          <summary className="min-h-9 cursor-pointer py-2 text-muted-foreground">
            {chapters.length} Planner chapters and their source files
          </summary>
          <ol className="flex flex-col gap-1.5 pl-4">
            {chapters.map((chapter, index) => (
              <li key={chapter.id ?? `${chapter.order ?? index}-${chapter.title ?? "chapter"}`}>
                <details className="rounded-sm border border-border px-3 py-2">
                  <summary className="min-h-9 cursor-pointer py-1 font-medium">
                    {chapter.order ?? index + 1}. {chapter.title || "Untitled chapter"}
                    <span className="ml-2 font-normal text-muted-foreground">
                      {chapter.synth_eligible ? "Included in Synth" : "Excluded from Synth"} ·{" "}
                      {chapter.n_sources} source {chapter.n_sources === 1 ? "file" : "files"}
                    </span>
                  </summary>
                  {!chapter.synth_eligible && chapter.synth_exclusion_reason ? (
                    <p className="pb-2 text-muted-foreground">{chapter.synth_exclusion_reason}</p>
                  ) : null}
                  <ul className="list-disc break-all pl-5 font-mono text-muted-foreground">
                    {chapter.source_files.map((file) => (
                      <li key={file}>{file}</li>
                    ))}
                  </ul>
                </details>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </>
  );
}
