import { Check } from "lucide-react";
import type { ReactNode } from "react";
import type { StreamStatus } from "@/shared/sse";

import { type BookUnits, percent } from "../domain/books";

/** How far the extraction is, how fast it goes, and whether the live connection is healthy. The numbers sit outside
 * the live region (they change on every page); only the connection line is announced. */
export function RunProgress({
  completed,
  total,
  units,
  ratePerMinute,
  streaming,
  streamStatus,
  statusText,
  successful = false,
  action,
  className = "mt-4",
}: {
  completed: number;
  total: number;
  units: BookUnits;
  ratePerMinute: number | null;
  streaming: boolean;
  streamStatus: StreamStatus;
  statusText: string;
  successful?: boolean;
  action?: ReactNode;
  className?: string;
}) {
  const done = percent(completed, total);
  return (
    <div className={className}>
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          Docling extraction
          {successful ? (
            <span
              role="img"
              aria-label="Completed successfully"
              title="Completed successfully"
              className="inline-flex text-success-text"
            >
              <Check className="size-4" aria-hidden="true" />
            </span>
          ) : null}
        </h3>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {statusText}
          </span>
          {action}
        </div>
      </div>
      <div
        role="progressbar"
        aria-label={`${units.plural[0]?.toUpperCase()}${units.plural.slice(1)} extracted`}
        aria-valuemin={0}
        aria-valuemax={total > 0 ? total : 100}
        aria-valuenow={total > 0 ? completed : 0}
        aria-valuetext={total > 0 ? `${completed} of ${total} ${units.plural}` : "not started"}
        className="mt-2 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div className="h-full bg-primary" style={{ width: `${done ?? 0}%` }} />
      </div>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="tabular">
          {completed} of {total > 0 ? total : "?"} {units.plural}
          {done !== null ? ` · ${done}%` : ""}
        </span>
        {ratePerMinute !== null ? (
          <span className="tabular">
            {ratePerMinute.toFixed(1)} {units.plural}/min
          </span>
        ) : null}
      </p>
      <p aria-live="polite" className="text-xs text-warning-text">
        {streaming && streamStatus === "reconnecting" ? "Connection lost — reconnecting…" : null}
      </p>
    </div>
  );
}
