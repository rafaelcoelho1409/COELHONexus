"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ContextBar } from "@/shared/layout/context-bar";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { ActionButton } from "../../components/action-button";
import { LibraryPicker } from "../../components/library-list";
import { BdOverlaysProvider } from "../../components/overlays";
import { useSnapshot } from "../../hooks/use-bd-data";
import { BOOK_PARAM, bookHref } from "../../params";
import { distillReadiness, isDistillLive, selectDistillRun } from "../domain/run-state";
import { useDistillActions, useDistillRuns } from "../hooks/use-distill-data";
import { DISTILL_RUN_CONTROLS_ENABLED, DISTILL_RUN_PARAM, distillHref } from "../params";
import { DistillRunPanel } from "./distill-run-panel";

export function DistillPage() {
  return (
    <BdOverlaysProvider>
      <Suspense fallback={<p className="p-4 text-sm text-muted-foreground">Loading Distill…</p>}>
        <DistillStage />
      </Suspense>
    </BdOverlaysProvider>
  );
}

function DistillStage() {
  const router = useRouter();
  const params = useSearchParams();
  const bookId = params.get(BOOK_PARAM);
  const requestedRunId = params.get(DISTILL_RUN_PARAM);
  return (
    <div className="min-w-0">
      <ContextBar
        start={
          <LibraryPicker
            selected={bookId}
            onOpen={(id) => router.push(distillHref(id))}
            showActions={false}
          />
        }
      />
      <div className="mx-auto w-full max-w-6xl p-4">
        {bookId ? (
          <DistillBookStage key={bookId} bookId={bookId} requestedRunId={requestedRunId} />
        ) : (
          <section
            aria-labelledby="distill-empty-title"
            className="rounded-md border border-border bg-card p-5"
          >
            <h2 id="distill-empty-title" className="text-lg font-semibold">
              Choose a book
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Select a book from the library to open its Distill runs and Learning Guides.
            </p>
            <Link
              className="mt-4 inline-flex min-h-9 items-center rounded-sm border border-border px-3 text-sm"
              href={bookHref()}
            >
              Go to Ingestion
            </Link>
          </section>
        )}
      </div>
    </div>
  );
}

function DistillBookStage({ bookId, requestedRunId }: { bookId: string; requestedRunId: string | null }) {
  const router = useRouter();
  const snapshot = useSnapshot(bookId);
  const runs = useDistillRuns(bookId);
  const actions = useDistillActions(bookId);

  if (snapshot.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Opening the book…
      </p>
    );
  }
  if (snapshot.isError) {
    return (
      <section role="alert" className="rounded-md border border-border bg-card p-4 text-sm">
        <p className="text-destructive-text">
          This book could not be opened — {apiErrorMessage(snapshot.error)}
        </p>
        <div className="mt-3">
          <ActionButton onClick={() => void snapshot.refetch()}>Try again</ActionButton>
        </div>
      </section>
    );
  }

  const book = snapshot.data;
  const readiness = distillReadiness(book);
  const selected = selectDistillRun(runs.data ?? [], requestedRunId);
  const hasActiveRun = (runs.data ?? []).some((run) => isDistillLive(run.status));
  const startRun = () => {
    if (!readiness.ready || hasActiveRun || !book.agentic) return;
    actions.start.mutate(book.agentic.agentic_run_id, {
      onSuccess: (result) => router.push(distillHref(bookId, result.run_id)),
    });
  };
  const retryRun = () => {
    if (selected) {
      actions.resume.mutate(selected.run_id, {
        onSuccess: (result) => router.push(distillHref(bookId, result.run_id)),
      });
    }
  };
  const stopRun = () => {
    if (selected) actions.cancel.mutate(selected.run_id);
  };
  const actionPending = actions.start.isPending || actions.resume.isPending || actions.cancel.isPending;
  const mutationError = actions.start.error ?? actions.resume.error ?? actions.cancel.error;
  const title = book.manifest.title.trim() || book.manifest.filename;

  return (
    <section aria-labelledby="distill-book-title" className="min-w-0">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="distill-book-title" className="break-words text-xl font-semibold tracking-tight">
            {title}
          </h2>
          <p className="mt-1 break-all text-sm text-muted-foreground">{book.manifest.filename}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {book.manifest.n_pages}{" "}
            {book.manifest.filename.toLowerCase().endsWith(".epub") ? "sections" : "pages"}
          </p>
        </div>
        <Link
          href={bookHref(bookId)}
          className="inline-flex min-h-9 items-center rounded-sm border border-border px-3 text-sm text-foreground"
        >
          Open source pages
        </Link>
      </header>

      <div className="mt-4 rounded-md border border-border bg-card p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-base font-semibold">Distill run</h3>
            <p className="mt-1 text-sm text-muted-foreground">{readiness.reason}</p>
          </div>
          {!selected || selected.status === "done" ? (
            <ActionButton
              onClick={startRun}
              disabled={!DISTILL_RUN_CONTROLS_ENABLED || !readiness.ready || hasActiveRun || actionPending}
            >
              {actionPending ? "Starting…" : selected ? "Create another guide" : "Start Distill"}
            </ActionButton>
          ) : selected.status === "failed" || selected.status === "cancelled" ? (
            <ActionButton onClick={retryRun} disabled={!DISTILL_RUN_CONTROLS_ENABLED || actionPending}>
              {actionPending ? "Resuming…" : "Resume Distill"}
            </ActionButton>
          ) : (
            <ActionButton onClick={stopRun} disabled={!DISTILL_RUN_CONTROLS_ENABLED || actionPending}>
              {actionPending ? "Stopping…" : "Stop Distill"}
            </ActionButton>
          )}
        </div>
        <p id="distill-controls-note" className="mt-3 text-xs text-muted-foreground" aria-live="polite">
          Run controls are disabled while Distill is being structured. Existing runs and guides remain
          available here.
        </p>
        {mutationError ? (
          <p role="alert" className="mt-2 text-sm text-destructive-text">
            {apiErrorMessage(mutationError)}
          </p>
        ) : null}
      </div>

      {runs.isPending ? (
        <p role="status" className="mt-4 text-sm text-muted-foreground">
          Loading Distill runs…
        </p>
      ) : runs.isError ? (
        <section role="alert" className="mt-4 rounded-md border border-border bg-card p-4 text-sm">
          <p className="text-destructive-text">
            Distill runs could not be loaded — {apiErrorMessage(runs.error)}
          </p>
          <div className="mt-3">
            <ActionButton onClick={() => void runs.refetch()}>Try again</ActionButton>
          </div>
        </section>
      ) : selected ? (
        <div className="mt-4">
          {(runs.data?.length ?? 0) > 1 ? (
            <label
              className="mb-2 block text-xs font-medium text-muted-foreground"
              htmlFor="distill-run-select"
            >
              Run history
              <select
                id="distill-run-select"
                value={selected.run_id}
                onChange={(event) => router.push(distillHref(bookId, event.currentTarget.value))}
                className="mt-1 block min-h-9 w-full max-w-xl rounded-sm border border-border bg-background px-2 text-sm text-foreground"
              >
                {runs.data?.map((run) => (
                  <option key={run.run_id} value={run.run_id}>
                    {run.status} · {new Date(run.created_at * 1000).toISOString()} · {run.run_id}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <DistillRunPanel bookId={bookId} run={selected} />
        </div>
      ) : (
        <section
          aria-labelledby="distill-no-runs"
          className="mt-4 rounded-md border border-border bg-card p-5"
        >
          <h3 id="distill-no-runs" className="text-base font-semibold">
            No Learning Guide yet
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Distill will create a source-linked guide after extraction and ingestion quality checks are
            complete.
          </p>
        </section>
      )}
    </section>
  );
}
