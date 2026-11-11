"use client";

import { Check, Circle, CircleAlert, LoaderCircle } from "lucide-react";
import type { JSX, ReactNode } from "react";
import { Badge } from "@/shared/ui/badge";

export type AgenticStepStatus = "queued" | "active" | "done" | "failed" | "skipped";

export type AgenticGraphStep = {
  id: string;
  label: string;
  description?: string;
  status: AgenticStepStatus;
  detail?: string;
  children?: Array<{ id: string; label: string; detail?: string; status: AgenticStepStatus }>;
};

const STATUS_LABEL: Record<AgenticStepStatus, string> = {
  queued: "Queued",
  active: "Running",
  done: "Done",
  failed: "Failed",
  skipped: "Not selected",
};

function StepIcon({ status }: { status: AgenticStepStatus }): JSX.Element {
  if (status === "active") return <LoaderCircle aria-hidden className="size-4 animate-spin text-primary" />;
  if (status === "done")
    return <Check aria-hidden className="size-4 text-emerald-600 dark:text-emerald-400" />;
  if (status === "failed") return <CircleAlert aria-hidden className="size-4 text-destructive" />;
  return <Circle aria-hidden className="size-4 text-muted-foreground" />;
}

function StepCard({ step, last }: { step: AgenticGraphStep; last: boolean }): JSX.Element {
  const active = step.status === "active";
  const failed = step.status === "failed";
  return (
    <li className="relative list-none pl-7">
      {!last ? (
        <span aria-hidden className="absolute top-7 bottom-[-0.75rem] left-[0.45rem] w-px bg-border" />
      ) : null}
      <span className="absolute top-1 left-0 z-10 flex size-4 items-center justify-center bg-background">
        <StepIcon status={step.status} />
      </span>
      <div
        className={`rounded-sm border px-3 py-2.5 ${
          active
            ? "border-primary/60 bg-primary/5"
            : failed
              ? "border-destructive/50 bg-destructive/5"
              : step.status === "done"
                ? "border-border bg-muted/40"
                : "border-border bg-background"
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          <h3 className="m-0 min-w-0 text-sm font-medium leading-5">{step.label}</h3>
          <Badge variant={failed ? "destructive" : active ? "default" : "outline"} className="shrink-0">
            {STATUS_LABEL[step.status]}
          </Badge>
        </div>
        {step.description ? (
          <p className="mt-1 mb-0 text-xs leading-4 text-muted-foreground">{step.description}</p>
        ) : null}
        {step.detail ? (
          <p className="mt-1 mb-0 break-words text-xs leading-4 text-muted-foreground">{step.detail}</p>
        ) : null}
        {step.children?.length ? (
          <ul
            aria-label={`${step.label} branches`}
            className="mt-2 mb-0 grid list-none gap-1.5 border-l border-border pl-3"
          >
            {step.children.map((child) => (
              <li key={child.id} className="flex min-w-0 items-start gap-2 text-xs">
                <StepIcon status={child.status} />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{child.label}</span>
                  {child.detail ? (
                    <span className="block break-words text-muted-foreground">{child.detail}</span>
                  ) : null}
                </span>
                <span className="shrink-0 text-muted-foreground">{STATUS_LABEL[child.status]}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  );
}

export function AgenticGraphPanel({
  title,
  description,
  route,
  status,
  steps,
  footer,
}: {
  title: string;
  description: string;
  route: string;
  status: "idle" | "running" | "done" | "failed";
  steps: AgenticGraphStep[];
  footer?: ReactNode;
}): JSX.Element {
  const statusLabel =
    status === "idle"
      ? "Ready"
      : status === "running"
        ? "Running"
        : status === "done"
          ? "Complete"
          : "Failed";
  return (
    <section aria-label={`${title} graph`} className="rounded-sm border border-border bg-background">
      <header className="border-b border-border px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="m-0 text-sm font-semibold tracking-tight">{title}</h2>
            <p className="mt-1 mb-0 text-xs leading-4 text-muted-foreground">{description}</p>
          </div>
          <Badge variant={status === "failed" ? "destructive" : status === "running" ? "default" : "outline"}>
            {statusLabel}
          </Badge>
        </div>
        <p className="mt-2 mb-0 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Route:</span> {route}
        </p>
      </header>
      <ol aria-label={`${title} execution steps`} className="m-0 flex list-none flex-col gap-3 px-4 py-4">
        {steps.map((step, index) => (
          <StepCard key={step.id} step={step} last={index === steps.length - 1} />
        ))}
      </ol>
      {footer ? <div className="border-t border-border px-4 py-3">{footer}</div> : null}
    </section>
  );
}
