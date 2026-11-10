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
import { BOOK_PARAM, bookHref, PAGE_PARAM, parsePageParam } from "../../params";
import { RagIndexBoard } from "../../rag/components/rag-index-board";
import { useRagCapabilities, useRagIndex, useRagIndexActions } from "../../rag/hooks/use-index-data";
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
import { DISTILL_RUN_PARAM, distillHref, distillStudyHref } from "../params";
import { DistillNodeBoard } from "./distill-node-board";
import { DistillOutputPanel } from "./distill-output-panel";
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
  const initialPage = parsePageParam(params.get(PAGE_PARAM));
  const library = useLibrary();
  const selectedBook = library.data?.find((book) => book.book_id === bookId);
  return (
    <div className="min-w-0">
      <ContextBar
        start={
          <>
            <LibraryPicker
              selected={bookId}
              onOpen={(id) => router.push(view === "study" ? distillStudyHref(id) : distillHref(id))}
              showActions={false}
              showSelectedTitle={false}
            />
            {selectedBook ? (
              <p className="text-xs text-muted-foreground">
                {selectedBook.n_pages} {bookUnits(selectedBook.filename).plural}
              </p>
            ) : null}
          </>
        }
        end={
          selectedBook ? (
            <p
              className="ml-auto min-w-0 max-w-[min(48vw,40rem)] break-words text-right text-base font-semibold text-foreground max-sm:w-full max-sm:max-w-full"
              title={selectedBook.title.trim() || selectedBook.filename}
            >
              {selectedBook.title.trim() || selectedBook.filename}
            </p>
          ) : undefined
        }
      />
      <div className="mx-auto w-full max-w-6xl p-4">
        {bookId ? (
          <DistillBookStage
            key={`${bookId}:${view}`}
            bookId={bookId}
            requestedRunId={requestedRunId}
            initialPage={initialPage}
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
                ? "Read the processed pages, review the selected source pages, and open the Learning Guide."
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
  initialPage,
  view,
}: {
  bookId: string;
  requestedRunId: string | null;
  initialPage: number | null;
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
  return (
    <DistillWorkspaceView
      bookId={bookId}
      requestedRunId={requestedRunId}
      initialPage={initialPage}
      view={view}
      book={book}
    />
  );
}

function DistillWorkspaceView({
  bookId,
  requestedRunId,
  initialPage,
  view,
  book,
}: {
  bookId: string;
  requestedRunId: string | null;
  initialPage: number | null;
  view: "agents" | "study";
  book: BookSnapshot;
}) {
  const router = useRouter();
  const { confirm, notice } = useBdOverlays();
  const capabilities = useDistillCapabilities();
  const ragCapabilities = useRagCapabilities();
  const runs = useDistillRuns(bookId);
  const actions = useDistillActions(bookId);
  const agenticRunId = book.agentic?.status === "done" ? book.agentic.agentic_run_id : null;
  const contentSelection = useContentSelection(bookId, agenticRunId);
  const saveSelection = useSaveContentSelection(bookId);
  const [stoppingRunId, setStoppingRunId] = useState<string | null>(null);
  const readiness = distillReadiness(book, contentSelection.data ?? null, contentSelection.isError);
  const selected = selectDistillRun(runs.data ?? [], requestedRunId);
  const ragIndex = useRagIndex(bookId, selected?.run_id ?? "", selected?.status === "done");
  const ragActions = useRagIndexActions(bookId, selected?.run_id ?? "");
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
  const startRag = () => {
    ragActions.start.mutate(undefined, {
      onSuccess: () => notice("Agentic RAG queued."),
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const stopRag = () => {
    ragActions.cancel.mutate(undefined, {
      onSuccess: (result) => {
        notice(result.status === "cancelled" ? "Agentic RAG stopped." : "Agentic RAG stop requested.");
      },
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const wipeRag = async () => {
    const confirmed = await confirm({
      title: "Wipe Agentic RAG index?",
      message:
        "Deletes this run’s retrieval vectors, book graph, and index manifest. Distill results and the uploaded book are preserved.",
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    ragActions.wipe.mutate(undefined, {
      onSuccess: () => notice("Agentic RAG index wiped."),
      onError: (error) => notice(apiErrorMessage(error), "error"),
    });
  };
  const progressActions = (
    <>
      <ActionButton
        onClick={resumable ? resumeRun : startRun}
        disabled={resumable ? resumeDisabled : startDisabled}
        aria-busy={actions.start.isPending || actions.resume.isPending}
        aria-label={resumable ? "Resume Distill" : "Start Distill"}
        title={
          !canExecute
            ? "Distill execution is unavailable in this environment."
            : runs.isPending
              ? "Loading Distill runs."
              : runs.isError
                ? "Load Distill runs before starting."
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
      key={`${selected?.run_id ?? "no-run"}:${agenticRunId ?? "no-ingestion-ai"}:${initialPage ?? "remembered"}`}
      bookId={bookId}
      book={book}
      initialPage={initialPage}
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
        <>
          <div className="mt-4 min-w-0 space-y-3">
            {progressPanel}
            {runHistory}
          </div>
          <div className="mt-4 grid min-w-0 grid-cols-1 items-start gap-4 xl:grid-cols-2">
            <div className={!runs.isPending && !runs.isError ? "min-w-0" : "min-w-0 xl:col-span-2"}>
              <DistillNodeBoard
                status={progressStatus}
                progress={progress}
                statusText={progressStatusText}
                actions={progressActions}
                runId={controlRun?.run_id ?? null}
              />
            </div>
            {!runs.isPending && !runs.isError ? (
              <RagIndexBoard
                index={ragIndex.data ?? null}
                distillComplete={selected?.status === "done"}
                executionEnabled={ragCapabilities.data?.execution_enabled === true}
                capabilityError={ragCapabilities.isError ? apiErrorMessage(ragCapabilities.error) : null}
                startPending={ragActions.start.isPending}
                cancelPending={ragActions.cancel.isPending}
                wipePending={ragActions.wipe.isPending}
                onStart={startRag}
                onCancel={stopRag}
                onWipe={() => void wipeRag()}
              />
            ) : null}
          </div>
        </>
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
