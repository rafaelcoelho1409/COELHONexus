"use client";

/** One line on how the latest plan was produced; says why when it ran on deterministic fallbacks. */

import { planHealthView } from "../domain/usage";
import { usePlannerHealth } from "../hooks/use-usage";

const TONE = { healthy: "text-success-text", degraded: "text-warning-text" } as const;

export function PlanHealth({ slug }: { slug: string }) {
  const health = usePlannerHealth(slug, true);
  const view = health ? planHealthView(health.degraded, health.pipeline_health) : null;
  if (!view || view.status === "unknown") return null;

  return (
    <div className="text-xs">
      <p>
        <span className="text-muted-foreground">Plan health: </span>
        <span className={`font-medium ${TONE[view.status]}`}>{view.status}</span>
        {view.status === "degraded" ? (
          <span className="text-muted-foreground">
            {" "}
            — it ran on fallbacks, so review it before synthesizing
          </span>
        ) : null}
      </p>
      {view.reasons.length > 0 ? (
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
          {view.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
