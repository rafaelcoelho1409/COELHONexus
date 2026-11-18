"use client";

import { Info } from "lucide-react";
import type { ReactNode } from "react";
import { ActionButton } from "../../components/action-button";
import type { DistillRun } from "../entities";

export function DistillProcessProgress({
  status,
  progress,
  statusText,
  error,
  controls,
  onOpenInfo,
}: {
  status: DistillRun["status"] | null;
  progress: DistillRun["progress"] | null;
  statusText: string;
  error: string | null;
  controls: ReactNode;
  onOpenInfo: () => void;
}) {
  const total = progress?.total_units ?? 0;
  const completed = progress ? Math.min(progress.completed_units, total) : 0;
  const percent = total > 0 ? Math.round((completed / total) * 100) : status === "done" ? 100 : 0;

  return (
    <ProgressCard
      title="Distill Agentic AI"
      status={statusText}
      detail=""
      percent={percent}
      progressLabel="Learning units"
      progressValue={
        total > 0
          ? `${completed.toLocaleString()} / ${total.toLocaleString()} · ${percent}%`
          : "Waiting for unit totals"
      }
      primaryMetric={[
        "Completed units",
        total > 0 ? `${completed.toLocaleString()} / ${total.toLocaleString()}` : "—",
      ]}
      secondaryMetric={[
        "Chapters",
        progress?.total_chapters ? `${progress.completed_chapters} / ${progress.total_chapters}` : "—",
      ]}
      error={error}
      controls={controls}
      onOpenInfo={onOpenInfo}
      infoLabel="Open learning progress"
    />
  );
}

function ProgressCard({
  title,
  status,
  detail,
  percent,
  progressLabel,
  progressValue,
  primaryMetric,
  secondaryMetric,
  error,
  controls,
  onOpenInfo,
  infoLabel,
}: {
  title: string;
  status: string;
  detail: string;
  percent: number;
  progressLabel: string;
  progressValue: string;
  primaryMetric: [string, string];
  secondaryMetric: [string, string];
  error?: string | null;
  controls: ReactNode;
  onOpenInfo: () => void;
  infoLabel: string;
}) {
  return (
    <section aria-label={`${title} progress`} className="min-w-0 rounded-md border border-border bg-card p-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{title}</h3>
        <div className="flex items-center gap-2">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {status}
          </span>
          <ActionButton
            iconOnly
            type="button"
            onClick={onOpenInfo}
            aria-haspopup="dialog"
            aria-label={infoLabel}
            title={infoLabel.replace(/^Open /, "")}
          >
            <Info aria-hidden />
          </ActionButton>
        </div>
      </header>
      <fieldset className="mt-2 grid min-w-0 grid-cols-3 gap-2 border-0 p-0">
        <legend className="sr-only">{title} controls</legend>
        {controls}
      </fieldset>
      <div className="mt-3 flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium">{progressLabel}</span>
        <span className="text-right tabular-nums text-muted-foreground">{progressValue}</span>
      </div>
      <div
        role="progressbar"
        aria-label={`${title} ${progressLabel.toLowerCase()}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${progressValue}; ${percent}%`}
        className="mt-1 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div className="h-full bg-primary" style={{ width: `${percent}%` }} />
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs">
        <Metric label={primaryMetric[0]} value={primaryMetric[1]} />
        <Metric label={secondaryMetric[0]} value={secondaryMetric[1]} />
      </dl>
      {detail ? <p className="mt-2 text-xs text-muted-foreground">{detail}</p> : null}
      {error ? (
        <p role="alert" className="mt-2 break-words text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-sm border border-border bg-background px-2.5 py-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-words font-medium tabular-nums text-foreground">{value}</dd>
    </div>
  );
}
