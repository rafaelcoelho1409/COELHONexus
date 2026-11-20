"use client";

import { Info } from "lucide-react";
import type { ReactNode } from "react";
import { ActionButton } from "../../components/action-button";

export function DistillProcessProgress({
  statusText,
  error,
  actionFeedback,
  startDisabledReason,
  controls,
  onOpenInfo,
}: {
  statusText: string;
  error: string | null;
  actionFeedback: { kind: "status" | "error"; text: string } | null;
  startDisabledReason: string | null;
  controls: ReactNode;
  onOpenInfo: () => void;
}) {
  return (
    <section
      aria-label="Distill Agentic AI progress"
      className="min-w-0 rounded-md border border-border bg-card p-3"
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-medium">Distill Agentic AI</h3>
          <span aria-live="polite" className="mt-1 block text-xs text-muted-foreground">
            {statusText}
          </span>
        </div>
        <ActionButton
          iconOnly
          type="button"
          onClick={onOpenInfo}
          aria-haspopup="dialog"
          aria-label="Open learning progress"
          title="Learning progress"
        >
          <Info aria-hidden />
        </ActionButton>
      </header>
      <fieldset className="mt-3 grid min-w-0 grid-cols-3 gap-1.5 border-0 p-0">
        <legend className="sr-only">Distill Agentic AI controls</legend>
        {controls}
      </fieldset>
      {actionFeedback ? (
        <p
          id="distill-start-feedback"
          role={actionFeedback.kind === "error" ? "alert" : "status"}
          className={`mt-2 break-words text-xs ${
            actionFeedback.kind === "error" ? "text-destructive-text" : "text-muted-foreground"
          }`}
        >
          {actionFeedback.text}
        </p>
      ) : startDisabledReason ? (
        <p
          id="distill-start-disabled-reason"
          role="status"
          className="mt-2 break-words text-xs text-muted-foreground"
        >
          {startDisabledReason}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 break-words text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
    </section>
  );
}
