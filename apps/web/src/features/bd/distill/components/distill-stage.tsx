"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
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
  useContentSelection,
  useDistillActions,
  useDistillCapabilities,
  useDistillRunStream,
  useDistillRuns,
  useSaveContentSelection,
} from "../hooks/use-distill-data";
import { DISTILL_CANCEL_RETRY_MS, DISTILL_RUN_PARAM, distillHref, distillStudyHref } from "../params";
import { DistillNodeBoard } from "./distill-node-board";
import { DistillOutputPanel } from "./distill-output-panel";
import { DistillProcessProgress } from "./distill-process-progress";
import { DistillProgressPanel } from "./distill-progress-panel";

export function DistillPage() {
  return <DistillWorkspacePage view="agents" />;
}

export function DistillStudyPage() {
  return <DistillWorkspacePage view="study" />;
}

function DistillWorkspacePage({ view }: { view: "agents" | "study" }) {
  return (
    <BdOverlaysProvider>
      <Suspense
        fallback={
          <p className="p-4 text-sm text-muted-foreground">
            {view === "study" ? "Loading Study…" : "Loading Distill…"}
          </p>
        }
      >
        <DistillStage view={view} />
      </Suspense>
    </BdOverlaysProvider>
  );
}

function DistillStage({ view }: { view: "agents" | "study" }) {
  const router = useRouter();
  const params = useSearchParams();
  const bookId = params.get(BOOK_PARAM);
  const requestedRunId = params.get(DISTILL_RUN_PARAM);
  const library = useLibrary();
  const selectedBook = library.data?.find((book) => book.book_id === bookId);
  return (
    <div className="min-w-0">
      <ContextBar
        pinned
        start={
          <>
            <LibraryPicker
              selected={bookId}
              onOpen={(id) => router.push(view === "study" ? distillStudyHref(id) : distillHref(id))}
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
      <div className="mx-auto w-full max-w-screen-2xl p-4">
        {bookId ? (
          <DistillBookStage
            key={`${bookId}:${view}`}
            bookId={bookId}
            requestedRunId={requestedRunId}
            view={view}
          />
        ) : (
          <section
            aria-labelledby="distill-empty-title"
            className="mx-auto max-w-2xl rounded-md border border-border bg-card p-6 sm:p-8"
          >
            <p className="mono-label">
              {view === "study" ? "Book Distiller · Study" : "Book Distiller · Distill"}
            </p>
            <h2 id="distill-empty-title" className="mt-2 text-xl font-semibold tracking-tight">
              {view === "study"
                ? "Choose a book to study its processed content"
                : "Choose a book to open its Distill Agents workspace"}
            </h2>
            <p className="mt-2 max-w-prose text-sm text-muted-foreground">
              {view === "study"
                ? "Read the Learning Guide generated from the book's selected source content."
                : "Review source readiness, follow Distill runs, and open the completed results in Study."}
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link
                className="inline-flex min-h-9 items-center rounded-sm border border-border px-3 text-sm"
                href={view === "study" ? "/bd/distill" : bookHref()}
              >
                {view === "study" ? "Open Distill Agents" : "Open Ingestion"}
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
  view,
}: {
  bookId: string;
  requestedRunId: string | null;
  view: "agents" | "study";
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
  return <DistillWorkspaceView bookId={bookId} requestedRunId={requestedRunId} view={view} book={book} />;
}

function DistillWorkspaceView({
  bookId,
  requestedRunId,
  view,
  book,
}: {
  bookId: string;
  requestedRunId: string | null;
  view: "agents" | "study";
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
  const [restartingQueuedRun, setRestartingQueuedRun] = useState(false);
  const [distillProgressOpen, setDistillProgressOpen] = useState(false);
  const [startFeedback, setStartFeedback] = useState<{
    kind: "status" | "error";
    text: string;
  } | null>(null);
  const [replayPendingNodeId, setReplayPendingNodeId] = useState<string | null>(null);
  const [replayMessage, setReplayMessage] = useState<{
    kind: "status" | "error";
    text: string;
  } | null>(null);
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
  const canReplay = Boolean(controlRun && ["done", "failed", "cancelled"].includes(progressStatus ?? ""));
  const stopping = activeRun !== null && stoppingRunId === activeRun.run_id;
  const actionPending =
    actions.start.isPending || actions.resume.isPending || actions.cancel.isPending || actions.wipe.isPending;
  useEffect(() => {
    if (stoppingRunId && activeRun?.run_id !== stoppingRunId) setStoppingRunId(null);
  }, [activeRun?.run_id, stoppingRunId]);
  useEffect(() => {
    if (!stoppingRunId || activeRun?.run_id !== stoppingRunId || actions.cancel.isPending) return;
    const retry = window.setTimeout(() => {
      actions.cancel.mutate(stoppingRunId, {
        onSuccess: (result) => {
          if (!isDistillLive(result.status)) setStoppingRunId(null);
        },
      });
    }, DISTILL_CANCEL_RETRY_MS);
    return () => window.clearTimeout(retry);
  }, [actions.cancel.isPending, actions.cancel.mutate, activeRun?.run_id, stoppingRunId]);
  const canExecute = capabilities.data?.execution_enabled === true;
  const canReplaceQueuedRun = activeRun?.status === "queued";
  const startDisabledReason = capabilities.isPending
    ? "Checking Distill availability…"
    : capabilities.isError
      ? "Distill availability could not be checked. Reload the page to try again."
      : !canExecute
        ? "Distill runs are unavailable in this environment."
        : runs.isPending
          ? "Loading Distill runs…"
          : runs.isError
            ? "Distill run history could not be loaded. Reload the page to try again."
            : !readiness.ready
              ? (readiness.steps.find((step) => step.status !== "complete")?.detail ?? readiness.reason)
              : !book.agentic || !contentSelection.data
                ? "The Ingestion run or page selection is no longer available. Reload the page to try again."
                : activeRun !== null && !canReplaceQueuedRun
                  ? "A Distill run is already active. Stop it before starting another run."
                  : actionPending
                    ? "Another Distill action is in progress."
                    : null;
  const startDisabled = startDisabledReason !== null;
  const resumeDisabled =
    !canExecute ||
    !resumable ||
    !controlRun ||
    activeRun !== null ||
    actionPending ||
    runs.isPending ||
    runs.isError;
  const stopDisabled = activeRun === null || stopping || actionPending;
  const wipeDisabled =
    selected === null || isDistillLive(selected.status) || activeRun !== null || actionPending;
  const queueFreshRun = () => {
    if (!book.agentic || !contentSelection.data) {
      setStartFeedback({
        kind: "error",
        text: "The Ingestion run or page selection is no longer available. Reload the page and try again.",
      });
      return;
    }
    setStartFeedback({ kind: "status", text: "Starting Distill…" });
    actions.start.mutate(book.agentic.agentic_run_id, {
      onSuccess: (result) => {
        setRestartingQueuedRun(false);
        setStoppingRunId(null);
        router.push(distillHref(bookId, result.run_id));
        setStartFeedback({ kind: "status", text: "Distill run queued. Loading progress…" });
        notice("Distill run queued.");
      },
      onError: (error) => {
        setRestartingQueuedRun(false);
        const message = apiErrorMessage(error);
        setStartFeedback({ kind: "error", text: `Could not start Distill — ${message}` });
        notice(message, "error");
      },
    });
  };
  const startRun = () => {
    if (startDisabled || !book.agentic || !contentSelection.data) {
      setStartFeedback({
        kind: "error",
        text:
          startDisabledReason ??
          "The Ingestion run or page selection is no longer available. Reload the page and try again.",
      });
      return;
    }
    if (canReplaceQueuedRun && activeRun) {
      setRestartingQueuedRun(true);
      setStartFeedback({ kind: "status", text: "Replacing the queued Distill run…" });
      actions.cancel.mutate(activeRun.run_id, {
        onSuccess: (result) => {
          if (result.status !== "cancelled") {
            setRestartingQueuedRun(false);
            const message = "The queued Distill run became active. Stop it before starting a fresh run.";
            setStartFeedback({ kind: "error", text: message });
            notice(message, "info");
            return;
          }
          queueFreshRun();
        },
        onError: (error) => {
          setRestartingQueuedRun(false);
          const message = apiErrorMessage(error);
          setStartFeedback({ kind: "error", text: `Could not replace the queued run — ${message}` });
          notice(message, "error");
        },
      });
      return;
    }
    queueFreshRun();
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
        } else if (result.status === "cancelled") {
          setStoppingRunId(null);
          notice("Distill run cancelled.");
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
  const replayDistillNode = async (nodeId: string) => {
    if (!controlRun || !canReplay || actions.replay.isPending) return;
    const confirmed = await confirm({
      title: "Replay from this stage?",
      message: `Clears Distill results from “${nodeId}” and every later stage, then reruns them. Earlier stage results are kept. This cannot be undone.`,
      confirmLabel: "Replay stage",
    });
    if (!confirmed) return;
    setReplayMessage(null);
    setReplayPendingNodeId(nodeId);
    actions.replay.mutate(
      { runId: controlRun.run_id, nodeId },
      {
        onSuccess: () => {
          setReplayPendingNodeId(null);
          const text = `Replay queued from ${nodeId}.`;
          setReplayMessage({ kind: "status", text });
          notice(`Distill ${text}`);
        },
        onError: (error) => {
          setReplayPendingNodeId(null);
          const message = apiErrorMessage(error);
          setReplayMessage({ kind: "error", text: `Replay failed — ${message}` });
          notice(message, "error");
        },
      },
    );
  };
  const progressActions = (
    <>
      <ActionButton
        type="button"
        className="w-full min-w-0 justify-center px-1"
        onClick={resumable ? resumeRun : startRun}
        disabled={resumable ? resumeDisabled : startDisabled}
        aria-describedby={
          !resumable
            ? startFeedback
              ? "distill-start-feedback"
              : startDisabledReason
                ? "distill-start-disabled-reason"
                : undefined
            : undefined
        }
        aria-busy={restartingQueuedRun || actions.start.isPending || actions.resume.isPending}
        aria-label={
          restartingQueuedRun
            ? "Restarting Distill"
            : resumable
              ? "Resume Distill"
              : canReplaceQueuedRun
                ? "Start a fresh Distill run"
                : "Start Distill"
        }
        title={
          !canExecute
            ? "Distill execution is unavailable in this environment."
            : runs.isPending
              ? "Loading Distill runs."
              : runs.isError
                ? "Load Distill runs before starting."
                : !readiness.ready
                  ? "Complete the source checks before starting Distill."
                  : canReplaceQueuedRun
                    ? "Cancel the queued run and start a fresh Distill run."
                    : activeRun
                      ? "A Distill run is already active."
                      : undefined
        }
      >
        {restartingQueuedRun
          ? "Restarting…"
          : actions.start.isPending || actions.resume.isPending
            ? resumable
              ? "Resuming…"
              : "Starting…"
            : resumable
              ? "Resume"
              : canReplaceQueuedRun
                ? "Start fresh"
                : "Start"}
      </ActionButton>
      <ActionButton
        type="button"
        className="w-full min-w-0 justify-center px-1"
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
        type="button"
        className="w-full min-w-0 justify-center px-1"
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
  );
  const progressStatusText = stopping
    ? "Stopping"
    : runs.isPending
      ? "Loading run history"
      : runs.isError
        ? "Run history unavailable"
        : progressStatus === "running"
          ? "Processing"
          : progressStatus
            ? statusLabel(progressStatus)
            : readiness.ready
              ? "Ready to run"
              : "Waiting for source checks";
  const progressPanel = (
    <DistillProgressPanel
      run={controlRun}
      status={progressStatus}
      progress={progress}
      error={progressError}
      streamStatus={progressStreamStatus}
      stopping={stopping}
      capabilitiesPending={capabilities.isPending}
      capabilitiesError={capabilities.isError ? apiErrorMessage(capabilities.error) : null}
      canExecute={canExecute}
    />
  );
  const runHistory = runs.isPending ? (
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
    <section aria-label="Learning Guide run history" className="rounded-md border border-border bg-card p-3">
      <label
        className="flex flex-wrap items-center gap-3 text-xs font-medium text-muted-foreground"
        htmlFor="distill-run-select"
      >
        <span>Run history · {runs.data?.length ?? 0} saved</span>
        <select
          id="distill-run-select"
          value={selected.run_id}
          onChange={(event) => router.push(distillHref(bookId, event.currentTarget.value), { scroll: false })}
          className="min-h-9 min-w-0 flex-1 rounded-sm border border-border bg-background px-2 text-sm font-normal text-foreground"
        >
          {runs.data?.map((run) => (
            <option key={run.run_id} value={run.run_id}>
              {statusLabel(run.status)} · {formatRunDate(run.created_at)} · run {run.run_id.slice(-8)}
            </option>
          ))}
        </select>
      </label>
    </section>
  ) : null;
  const outputPanel = (
    <DistillOutputPanel
      key={selected?.run_id ?? "no-run"}
      bookId={bookId}
      agenticRunId={agenticRunId}
      selection={contentSelection.data ?? null}
      selectionPending={agenticRunId !== null && contentSelection.isPending}
      selectionError={contentSelection.isError ? contentSelection.error : null}
      savingSelection={saveSelection.isPending}
      saveSelectionError={saveSelection.isError ? saveSelection.error : null}
      onRetrySelection={() => void contentSelection.refetch()}
      onSaveSelection={savePageSelection}
      run={selected}
      runState={selectedState}
      streamStatus={stream.status}
      runsPending={runs.isPending}
      runsError={runs.isError ? runs.error : null}
      onRetryRuns={() => void runs.refetch()}
    />
  );

  return (
    <section
      aria-label={view === "study" ? "Study workspace" : "Distill Agents workspace"}
      className="min-w-0"
    >
      {view === "study" ? (
        <div className="min-w-0">{outputPanel}</div>
      ) : (
        <div className="mt-4 min-w-0 space-y-4">
          <DistillNodeBoard
            bookId={bookId}
            status={progressStatus}
            progress={progress}
            statusText={progressStatusText}
            runId={controlRun?.run_id ?? null}
            processProgress={
              <DistillProcessProgress
                statusText={progressStatusText}
                error={progressError}
                actionFeedback={startFeedback}
                startDisabledReason={resumable ? null : startDisabledReason}
                controls={progressActions}
                onOpenInfo={() => setDistillProgressOpen(true)}
              />
            }
            progressDetails={progressPanel}
            progressOpen={distillProgressOpen}
            onProgressOpenChange={setDistillProgressOpen}
            onPlayNode={(nodeId) => void replayDistillNode(nodeId)}
            replayDisabled={!canReplay || actionPending}
            replayPending={actions.replay.isPending}
            replayPendingNodeId={replayPendingNodeId}
            replayMessage={replayMessage}
          />
          {runHistory}
        </div>
      )}
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
