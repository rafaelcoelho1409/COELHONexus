"use client";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { bookProgressText, isTerminalTaskState, progressText } from "../domain";
import { useEmbeddingMigration } from "../hooks/use-embedding-migration";
import { SettingsActionButton } from "./settings-action-button";

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
  const { status, view, start, task, bookTask } = useEmbeddingMigration();

  const outcome = task ? statusOf(task.state) : { tone: "busy" as const, text: "Starting…" };
  const bookOutcome = bookTask ? statusOf(bookTask.state) : { tone: "busy" as const, text: "Starting…" };
  const books = status?.book_migration;
  const bookRunning = books?.state?.status === "running" || books?.state?.status === "queued";
  const bookNeeded = books?.connected === true && books.needed && books.book_count > 0;
  const videoNeeded = view.kind === "needed" && status?.embedding_connected !== false;

  return (
    <div className="flex flex-col gap-3">
      {status?.active_collection && (
        <p className="font-mono text-xs text-muted-foreground">
          {"Active collection: "}
          <span className="text-foreground">{status.active_collection}</span>
        </p>
      )}
      {status?.video_active_collection && (
        <p className="font-mono text-xs text-muted-foreground">
          {"Video search collection: "}
          <span className="text-foreground">{status.video_active_collection}</span>
        </p>
      )}
      {books?.connected === false && (
        <p role="status" className="text-sm text-muted-foreground">
          Test the saved embedding connection successfully to check the book index and migration target.
        </p>
      )}
      {books && books.inventory_errors > 0 && (
        <p role="alert" className="text-sm text-destructive-text">
          Couldn&apos;t inspect every published Book Distiller index. Migration is paused until storage is
          available.
        </p>
      )}
      {videoNeeded && !bookNeeded && view.kind === "needed" && (
        <div className="border border-border bg-background p-4">
          <p className="text-sm text-foreground">
            Embedding model changed from <span className="font-medium">{view.from}</span> to{" "}
            <span className="font-medium">{view.to}</span>. Previously ingested videos won&apos;t be
            searchable until they are re-embedded.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <SettingsActionButton
              type="button"
              onClick={() => start.mutate()}
              disabled={start.isPending}
              aria-busy={start.isPending}
            >
              {start.isPending ? "Starting…" : "Migrate now"}
            </SettingsActionButton>
          </div>
          {start.isError && (
            <p role="alert" className="mt-2 text-sm text-destructive-text">
              {apiErrorMessage(start.error)}
            </p>
          )}
        </div>
      )}
      {bookNeeded && books && (
        <div className="border border-border bg-background p-4">
          <p className="text-sm text-foreground">
            {books.book_count} indexed {books.book_count === 1 ? "book uses" : "books use"} an older embedding
            profile. The target collection is{" "}
            <span className="font-mono text-xs">{books.target_collection}</span>.
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            Each book switches after its new vectors are ready. Its existing vectors remain in the old
            collection for recovery.
          </p>
          {books.mismatches.map((mismatch) => (
            <p
              key={`${mismatch.from_model}:${mismatch.from_dimensions}`}
              className="mt-2 font-mono text-xs text-muted-foreground"
            >
              {mismatch.book_count} {mismatch.book_count === 1 ? "book" : "books"}: {mismatch.from_model} ·{" "}
              {mismatch.from_dimensions} dimensions
            </p>
          ))}
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <SettingsActionButton
              type="button"
              onClick={() => start.mutate()}
              disabled={start.isPending || bookRunning || books.inventory_errors > 0}
              aria-busy={start.isPending}
            >
              {start.isPending ? "Starting…" : bookRunning ? "Migration running…" : "Migrate books"}
            </SettingsActionButton>
            {start.isError && (
              <p role="alert" className="text-sm text-destructive-text">
                {apiErrorMessage(start.error)}
              </p>
            )}
          </div>
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
      {bookRunning && (
        <div className="border border-border bg-background p-4">
          <p className="text-sm text-foreground">
            Migrating {books?.state?.total ?? books?.book_count ?? 0} book indexes to{" "}
            {books?.state?.to_model ?? books?.target_model}…
          </p>
          <p aria-live="polite" className={`mt-2 font-mono text-xs ${STATUS_TONE[bookOutcome.tone]}`}>
            {bookOutcome.tone === "busy" ? bookProgressText(bookTask) : bookOutcome.text}
          </p>
        </div>
      )}
      {books?.state?.status === "complete" && (
        <p role="status" className="text-sm text-success-text">
          Book embedding migration complete · {books.state.completed ?? books.book_count} books migrated.{" "}
          Previous vectors remain available in their original collection.
        </p>
      )}
      {books?.state?.status === "failed" && (
        <p role="alert" className="text-sm text-destructive-text">
          {books.state.error ??
            `${books.state.failed ?? 0} book indexes could not be migrated. You can retry the migration.`}
        </p>
      )}
      {videoNeeded && bookNeeded && (
        <p className="text-xs text-muted-foreground">
          Migrate books also starts the pending transcript migration.
        </p>
      )}
    </div>
  );
}
