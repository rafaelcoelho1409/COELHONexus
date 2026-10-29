"use client";

/** How the latest plan was produced, beside the planner's status in its run bar (a flex-wrap row): one short
 * "Plan health: healthy" item, and — only when it ran on deterministic fallbacks — a full-width line saying so with
 * the reasons under it. */

import { planHealthView } from "../domain/usage";
import { usePlannerHealth } from "../hooks/use-usage";

const TONE = { healthy: "text-success-text", degraded: "text-warning-text" } as const;

export function PlanHealth({ slug }: { slug: string }) {
  const health = usePlannerHealth(slug, true);
  const view = health ? planHealthView(health.degraded, health.pipeline_health) : null;
  if (!view || view.status === "unknown") return null;

  return (
    <>
      <p className="text-xs">
        <span className="text-muted-foreground">Plan health: </span>
        <span className={`font-medium ${TONE[view.status]}`}>{view.status}</span>
      </p>
      {view.status === "degraded" || view.reasons.length > 0 ? (
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
    </>
  );
}
