"use client";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";

import { isTerminalTaskState, progressText } from "../domain";
import { useEmbeddingMigration } from "../hooks/use-embedding-migration";

const STATUS_TONE = {
  complete: "text-success-text",
  failed: "text-destructive-text",
  busy: "text-muted-foreground",
} as const;

function statusOf(state: string): { tone: keyof typeof STATUS_TONE; text: string } {
  if (state === "SUCCESS") return { tone: "complete", text: "Embedding migration complete" };
  if (isTerminalTaskState(state))
    return { tone: "failed", text: "Embedding migration failed — check the worker logs" };
  return { tone: "busy", text: "Starting…" };
}

/** Decision is server-driven: hidden → nothing at all; needed → the ask; running → the progress. */
export function MigrationBanner() {
  const { status, view, start, task } = useEmbeddingMigration();

  const outcome = task ? statusOf(task.state) : { tone: "busy" as const, text: "Starting…" };

  return (
    <div className="flex flex-col gap-3">
      {status?.active_collection && (
        <p className="font-mono text-xs text-muted-foreground">
          {"Active collection: "}
          <span className="text-foreground">{status.active_collection}</span>
        </p>
      )}
      {view.kind === "needed" && (
        <div className="border border-border bg-background p-4">
          <p className="text-sm text-foreground">
            Embedding model changed from <span className="font-medium">{view.from}</span> to{" "}
            <span className="font-medium">{view.to}</span>. Previously ingested videos won&apos;t be
            searchable until they are re-embedded.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button
              type="button"
              size="lg"
              onClick={() => start.mutate()}
              disabled={start.isPending}
              aria-busy={start.isPending}
            >
              {start.isPending ? "Starting…" : "Migrate now"}
            </Button>
          </div>
          {start.isError && (
            <p role="alert" className="mt-2 text-sm text-destructive-text">
              {apiErrorMessage(start.error)}
            </p>
          )}
        </div>
      )}
      {view.kind === "running" && (
        <div className="border border-border bg-background p-4">
          <p className="text-sm text-foreground">
            Migrating {view.from} → {view.to}…
          </p>
          <p aria-live="polite" className={`mt-2 font-mono text-xs ${STATUS_TONE[outcome.tone]}`}>
            {outcome.tone === "busy" ? progressText(task) : outcome.text}
          </p>
        </div>
      )}
    </div>
  );
}
