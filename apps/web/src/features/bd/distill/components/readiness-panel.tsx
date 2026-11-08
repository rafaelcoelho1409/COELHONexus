import { Check, Circle, TriangleAlert } from "lucide-react";

import type { DistillReadiness } from "../domain/run-state";

const STATUS_LABEL = {
  complete: "Complete",
  waiting: "Waiting",
  attention: "Needs attention",
} as const;

export function ReadinessPanel({ readiness }: { readiness: DistillReadiness }) {
  const needsAttention = readiness.steps.some((step) => step.status === "attention");
  const readinessLabel = readiness.ready ? "Ready" : needsAttention ? "Needs attention" : "Waiting";
  return (
    <div>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 id="distill-readiness-title" className="text-sm font-medium">
            Source readiness
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">{readiness.reason}</p>
        </div>
        <span
          className={`inline-flex min-h-7 shrink-0 items-center rounded-sm border px-2 text-xs font-medium ${
            readiness.ready
              ? "border-success/40 bg-success/10 text-success-text"
              : needsAttention
                ? "border-warning/40 bg-warning/10 text-warning-text"
                : "border-border text-muted-foreground"
          }`}
        >
          {readinessLabel}
        </span>
      </header>

      <ol aria-label="Source readiness checks" className="mt-3 divide-y divide-border">
        {readiness.steps.map((step) => (
          <li key={step.id} className="flex min-w-0 items-start gap-2 py-3 first:pt-0 last:pb-0">
            <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center text-muted-foreground">
              {step.status === "complete" ? (
                <Check aria-hidden className="size-4 text-success-text" />
              ) : step.status === "attention" ? (
                <TriangleAlert aria-hidden className="size-4 text-warning-text" />
              ) : (
                <Circle aria-hidden className="size-4 text-muted-foreground" />
              )}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <h5 className="text-xs font-medium">{step.label}</h5>
                <span
                  className={`text-xs ${
                    step.status === "complete"
                      ? "text-success-text"
                      : step.status === "attention"
                        ? "text-warning-text"
                        : "text-muted-foreground"
                  }`}
                >
                  {STATUS_LABEL[step.status]}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
