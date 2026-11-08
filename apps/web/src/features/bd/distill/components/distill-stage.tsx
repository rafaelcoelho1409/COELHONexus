"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ContextBar } from "@/shared/layout/context-bar";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { ActionButton } from "../../components/action-button";
import { LibraryPicker } from "../../components/library-list";
import { BdOverlaysProvider } from "../../components/overlays";
import { bookUnits } from "../../domain/books";
import type { BookSnapshot } from "../../entities";
import { useLibrary, useSnapshot } from "../../hooks/use-bd-data";
import { useBdOverlays } from "../../hooks/use-overlays";
import { BOOK_PARAM, bookHref } from "../../params";
import {
  distillReadiness,
  initialDistillRunState,
  isDistillLive,
  selectDistillRun,
} from "../domain/run-state";
import {
  useDistillActions,
  useDistillCapabilities,
  useDistillRunStream,
  useDistillRuns,
  useContentSelection,
  useSaveContentSelection,
} from "../hooks/use-distill-data";
import { DISTILL_RUN_PARAM, distillHref } from "../params";
import { DistillNodeBoard } from "./distill-node-board";
import { DistillProgressPanel } from "./distill-progress-panel";
import { DistillRunPanel } from "./distill-run-panel";
import { ReadinessPanel } from "./readiness-panel";
import { ContentSelectionReview } from "./content-selection-review";

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
  const library = useLibrary();
  const selectedBook = library.data?.find((book) => book.book_id === bookId);
  return (
    <div className="min-w-0">
      <ContextBar
        start={
          <>
            <LibraryPicker
              selected={bookId}
              onOpen={(id) => router.push(distillHref(id))}
              showActions={false}
            />
            {selectedBook ? (
              <p className="text-xs text-muted-foreground">
                {selectedBook.n_pages} {bookUnits(selectedBook.filename).plural}
              </p>
            ) : null}
          </>
        }
      />
      <div className="mx-auto w-full max-w-6xl p-4">
        {bookId ? (
          <DistillBookStage
            key={bookId}
            bookId={bookId}
            requestedRunId={requestedRunId}
          />
        ) : (
          <section
            aria-labelledby="distill-empty-title"
            className="mx-auto max-w-2xl rounded-md border border-border bg-card p-6 sm:p-8"
          >
            <p className="mono-label">Book Distiller · Distill</p>
            <h2 id="distill-empty-title" className="mt-2 text-xl font-semibold tracking-tight">
              Choose a book to open its Learning Guide workspace
            </h2>
            <p className="mt-2 max-w-prose text-sm text-muted-foreground">
              Review a book’s source readiness, follow a Distill run, and read the report with citations back
              to its pages.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link
                className="inline-flex min-h-9 items-center rounded-sm border border-border px-3 text-sm"
                href={bookHref()}
              >
                Open Ingestion
              </Link>
              {library.data?.length ? (
                <p className="self-center text-xs text-muted-foreground">
                  Or choose a book from the library above.
                </p>
              ) : null}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function DistillBookStage({
  bookId,
  requestedRunId,
}: {
  bookId: string;
  requestedRunId: string | null;
}) {
  const snapshot = useSnapshot(bookId);

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
  return <DistillAgentsView bookId={bookId} requestedRunId={requestedRunId} book={book} />;
}

function DistillAgentsView({
  bookId,
  requestedRunId,
  book,
}: {
  bookId: string;
  requestedRunId: string | null;
  book: BookSnapshot;
}) {
  const router = useRouter();
  const { confirm, notice } = useBdOverlays();
  const capabilities = useDistillCapabilities();
  const runs = useDistillRuns(bookId);
  const actions = useDistillActions(bookId);
  const agenticRunId = book.agentic?.status === "done" ? book.agentic.agentic_run_id : null;
  const contentSelection = useContentSelection(bookId, agenticRunId);
  const saveSelection = useSaveContentSelection(bookId);
  const [stoppingRunId, setStoppingRunId] = useState<string | null>(null);
  const readiness = distillReadiness(book, contentSelection.data ?? null, contentSelection.isError);
  const selected = selectDistillRun(runs.data ?? [], requestedRunId);
  const activeRun = (runs.data ?? []).find((run) => isDistillLive(run.status)) ?? null;
  const controlRun = activeRun ?? selected;
  const stream = useDistillRunStream(bookId, selected);
  const selectedState = selected ? (stream.state ?? initialDistillRunState(selected)) : null;
  const progressStatus =
    controlRun && selected?.run_id === controlRun.run_id
      ? (selectedState?.status ?? controlRun.status)
      : (controlRun?.status ?? null);
  const progress =
    controlRun && selected?.run_id === controlRun.run_id
      ? (selectedState?.progress ?? controlRun.progress)
      : (controlRun?.progress ?? null);
  const progressError =
    controlRun && selected?.run_id === controlRun.run_id
      ? (selectedState?.error ?? null)
      : (controlRun?.error ?? null);
  const progressStreamStatus =
    controlRun && selected?.run_id === controlRun.run_id ? stream.status : "closed";
  const resumable = progressStatus === "failed" || progressStatus === "cancelled";
  const stopping = activeRun !== null && stoppingRunId === activeRun.run_id;
  const actionPending =
    actions.start.isPending || actions.resume.isPending || actions.cancel.isPending || actions.wipe.isPending;
  const canExecute = capabilities.data?.execution_enabled === true;
  const startDisabled =
    !canExecute || !readiness.ready || activeRun !== null || actionPending || runs.isPending || runs.isError;
  const resumeDisabled =
    !canExecute || !resumable || !controlRun || activeRun !== null || actionPending || runs.isPending || runs.isError;
  const stopDisabled = activeRun === null || stopping || actionPending;
  const wipeDisabled =
    selected === null || isDistillLive(selected.status) || activeRun !== null || actionPending;
  const startRun = () => {
    if (startDisabled || !book.agentic || !contentSelection.data) return;
    actions.start.mutate(book.agentic.agentic_run_id, {
      onSuccess: (result) => {
        setStoppingRunId(null);
        router.push(distillHref(bookId, result.run_id));
        notice("Distill run queued.");
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const resumeRun = () => {
    if (resumeDisabled || !controlRun) return;
    actions.resume.mutate(controlRun.run_id, {
      onSuccess: (result) => {
        setStoppingRunId(null);
        router.push(distillHref(bookId, result.run_id));
        notice("Distill run queued to resume.");
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const savePageSelection = (
    expectedRevision: number,
    overrides: { page_number: number; disposition: "include" | "exclude" }[],
  ) => {
    if (!agenticRunId || overrides.length === 0) return;
    saveSelection.mutate(
      { agenticRunId, expectedRevision, overrides },
      {
        onSuccess: () => notice("Page selection saved. New Distill runs will use this revision."),
        onError: (error) => notice(apiErrorMessage(error), "error"),
      },
    );
  };
  const stopRun = () => {
    if (stopDisabled || !activeRun) return;
    actions.cancel.mutate(activeRun.run_id, {
      onSuccess: (result) => {
        if (isDistillLive(result.status)) {
          setStoppingRunId(activeRun.run_id);
          notice("Distill stop requested; active work is draining.");
        } else {
          notice(`Distill run is already ${statusLabel(result.status).toLowerCase()}.`);
        }
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const wipeRun = async () => {
    if (wipeDisabled || !selected) return;
    const confirmed = await confirm({
      title: "Wipe Distill run?",
      message:
        "Deletes this run’s Learning Guide, intermediate results, retrieval index, usage counters, and checkpoints. The uploaded book and Ingestion results are preserved. This cannot be undone.",
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    actions.wipe.mutate(selected.run_id, {
      onSuccess: () => {
        router.replace(distillHref(bookId));
        notice("Distill run wiped. The book and Ingestion results are unchanged.");
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const title = book.manifest.title.trim() || book.manifest.filename;

  return (
    <section aria-labelledby="distill-book-title" className="min-w-0 space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div className="min-w-0">
          <p className="mono-label">Distill · Learning Guide</p>
          <h2 id="distill-book-title" className="mt-1 break-words text-xl font-semibold tracking-tight">
            {title}
          </h2>
        </div>
      </header>

      <div className="grid grid-cols-1 items-stretch gap-3 lg:grid-cols-2">
        <section
          aria-label="Distill source readiness"
          className="min-w-0 rounded-md border border-border bg-card p-3"
        >
          <ReadinessPanel readiness={readiness} />
        </section>
        <DistillProgressPanel
          run={controlRun}
          status={progressStatus}
          progress={progress}
          error={progressError}
          streamStatus={progressStreamStatus}
          readiness={readiness}
          stopping={stopping}
          capabilitiesPending={capabilities.isPending}
          capabilitiesError={capabilities.isError ? apiErrorMessage(capabilities.error) : null}
          canExecute={canExecute}
          actions={
            <>
              <ActionButton
                onClick={resumable ? resumeRun : startRun}
                disabled={resumable ? resumeDisabled : startDisabled}
                aria-busy={actions.start.isPending || actions.resume.isPending}
                aria-label={resumable ? "Resume Distill" : "Start Distill"}
                title={
                  !canExecute
                    ? "Distill execution is unavailable in this environment."
                    : !readiness.ready
                      ? "Complete the source checks before starting Distill."
                      : activeRun
                        ? "A Distill run is already active."
                        : undefined
                }
              >
                {actions.start.isPending || actions.resume.isPending
                  ? resumable
                    ? "Resuming…"
                    : "Starting…"
                  : resumable
                    ? "Resume"
                    : "Start"}
              </ActionButton>
              <ActionButton
                onClick={stopRun}
                disabled={stopDisabled}
                aria-busy={actions.cancel.isPending || stopping}
                aria-label="Stop Distill"
                title={
                  stopping
                    ? "Stop requested; active work is draining."
                    : activeRun
                      ? undefined
                      : "There is no active Distill run to stop."
                }
              >
                {actions.cancel.isPending || stopping ? "Stopping…" : "Stop"}
              </ActionButton>
              <ActionButton
                onClick={() => void wipeRun()}
                disabled={wipeDisabled}
                aria-busy={actions.wipe.isPending}
                aria-label="Wipe Distill"
                title={
                  !selected
                    ? "There are no Distill artifacts to wipe."
                    : activeRun
                      ? "Stop the active Distill run before wiping."
                      : undefined
                }
              >
                {actions.wipe.isPending ? "Wiping…" : "Wipe"}
              </ActionButton>
            </>
          }
        />
      </div>

      <ContentSelectionReview
        bookId={bookId}
        agenticRunId={agenticRunId}
        selection={contentSelection.data ?? null}
        pending={agenticRunId !== null && contentSelection.isPending}
        error={contentSelection.isError ? contentSelection.error : null}
        saving={saveSelection.isPending}
        saveError={saveSelection.isError ? saveSelection.error : null}
        onRetry={() => void contentSelection.refetch()}
        onSave={savePageSelection}
      />

      {runs.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading Distill runs…
        </p>
      ) : runs.isError ? (
        <section role="alert" className="rounded-md border border-border bg-card p-4 text-sm">
          <p className="text-destructive-text">
            Distill runs could not be loaded — {apiErrorMessage(runs.error)}
          </p>
          <div className="mt-3">
            <ActionButton onClick={() => void runs.refetch()}>Try again</ActionButton>
          </div>
        </section>
      ) : (runs.data?.length ?? 0) > 1 && selected ? (
        <section aria-label="Learning Guide run history" className="border-b border-border pb-3">
          <label
            className="flex flex-wrap items-center gap-3 text-xs font-medium text-muted-foreground"
            htmlFor="distill-run-select"
          >
            <span>Run history · {runs.data?.length ?? 0} saved</span>
            <select
              id="distill-run-select"
              value={selected.run_id}
              onChange={(event) =>
                router.push(distillHref(bookId, event.currentTarget.value), { scroll: false })
              }
              className="min-h-9 min-w-0 flex-1 rounded-sm border border-border bg-background px-2 text-sm font-normal text-foreground sm:max-w-2xl"
            >
              {runs.data?.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {statusLabel(run.status)} · {formatRunDate(run.created_at)} · run {run.run_id.slice(-8)}
                </option>
              ))}
            </select>
          </label>
        </section>
      ) : null}

      {!runs.isPending && !runs.isError ? (
        <DistillNodeBoard status={selectedState?.status ?? null} progress={selectedState?.progress ?? null} />
      ) : null}

      {selected && selectedState ? (
        <DistillRunPanel
          key={selected.run_id}
          bookId={bookId}
          run={selected}
          state={selectedState}
          streamStatus={stream.status}
        />
      ) : null}
    </section>
  );
}

function statusLabel(status: "queued" | "running" | "done" | "failed" | "cancelled"): string {
  if (status === "done") return "Complete";
  if (status === "queued") return "Queued";
  if (status === "running") return "Running";
  if (status === "failed") return "Failed";
  return "Cancelled";
}

function formatRunDate(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString();
}
