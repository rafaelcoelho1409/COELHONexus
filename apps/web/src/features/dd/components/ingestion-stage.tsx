"use client";

/** Ingestion stage: live run progress box + library explorer. Port of
 * ingestion/body.py + manifest.js + polling.js + explorer.js. */

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";

import {
  getDdIngestionQueryKey,
  getDdIngestionSlugManifestOptions,
  getDdRunsActiveOptions,
  getDdRunsRunIdOptions,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import {
  ingressSummary,
  isRunTerminal,
  runFramework,
  runProgressView,
  runToAdopt,
  tierLabel,
} from "../domain/ingestion";
import {
  useActiveRun,
  useCancelRun,
  useCatalog,
  useLibrary,
  useManifest,
  useRunPoll,
} from "../hooks/use-dd-data";
import { useDdOverlays } from "../hooks/use-overlays";
import { ingestionHref } from "../params";
import { Explorer } from "./explorer";
import { LogoStrip } from "./logo-strip";

export function IngestionStage({ slug, runId }: { slug: string | null; runId: string | null }) {
  const { runs } = useActiveRun(true);
  const router = useRouter();
  // Runs this page has watched finish. The list of active runs is cached for a few seconds, so right after a run
  // ends it can still name it; without this, the recovery below would put `?run=` straight back.
  const finished = useRef<Set<string>>(new Set());
  // Stable, because the progress box lists it among its effect's dependencies.
  const rememberFinished = useCallback((id: string) => {
    finished.current.add(id);
  }, []);

  // Page-reload recovery: with no ?run=, hop to an in-flight run for the
  // same framework (legacy recovery.js).
  useEffect(() => {
    const adopt = runToAdopt({
      slug,
      urlRunId: runId,
      active: runs.find((candidate) => candidate.slug === slug),
      finished: finished.current,
    });
    if (adopt && slug) router.replace(ingestionHref(slug, adopt), { scroll: false });
  }, [runId, slug, runs, router]);

  if (!slug) {
    return (
      <div className="rounded-md border border-border bg-card p-8 text-center">
        <h2 className="text-base font-medium">Ingest something first</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick a framework from the catalog — its progress and library will show up here.
        </p>
      </div>
    );
  }
  return <IngestionWorkspace slug={slug} runId={runId} onRunFinished={rememberFinished} />;
}

function IngestionWorkspace({
  slug,
  runId,
  onRunFinished,
}: {
  slug: string;
  runId: string | null;
  onRunFinished: (runId: string) => void;
}) {
  const manifestQuery = useManifest(slug);
  return (
    <div>
      {manifestQuery.data ? (
        <p className="mb-4 text-sm text-muted-foreground">{ingressSummary(manifestQuery.data)}</p>
      ) : null}
      {/* `key`: a new run starts from a clean box — its cancel request, not the last run's. */}
      {runId ? <RunProgressBox key={runId} slug={slug} runId={runId} onFinished={onRunFinished} /> : null}
      <Explorer slug={slug} manifest={manifestQuery.data} />
    </div>
  );
}

function RunProgressBox({
  slug,
  runId,
  onFinished,
}: {
  slug: string;
  runId: string;
  onFinished: (runId: string) => void;
}) {
  const statusQuery = useRunPoll(runId);
  const cancel = useCancelRun();
  const router = useRouter();
  const queryClient = useQueryClient();
  const catalog = useCatalog();
  const library = useLibrary();
  const framework = runFramework(slug, catalog.data, library.data);

  const { notice } = useDdOverlays();
  // The latest `notice`, read from inside the effect below without making the effect re-run when it changes.
  const noticeRef = useRef(notice);
  useEffect(() => {
    noticeRef.current = notice;
  });

  const progress = statusQuery.data?.progress ?? null;
  const status = progress?.status ?? null;
  const terminal = isRunTerminal(status);
  const failed = status === "failed";

  // Terminal transition: refresh everything the run touched, then leave the run's address. A finished run drops
  // `?run=` (the box goes away); a cancelled one says so first; a failed one stays on screen with its reason until the
  // reader dismisses it — dropping it at once hid the only explanation.
  useEffect(() => {
    if (!terminal) {
      return;
    }
    // Remember it first, so nothing can put `?run=` back, then refresh what the run touched. `cancelRefetch:
    // false` lets a refetch already under way finish instead of restarting it.
    onFinished(runId);
    const keepGoing = { cancelRefetch: false };
    void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() }, keepGoing);
    void queryClient.invalidateQueries(getDdIngestionSlugManifestOptions({ path: { slug } }), keepGoing);
    void queryClient.invalidateQueries(getDdRunsActiveOptions(), keepGoing);
    void queryClient.invalidateQueries(getDdRunsRunIdOptions({ path: { run_id: runId } }), keepGoing);
    if (status === "failed") {
      return;
    }
    if (status === "cancelled") {
      noticeRef.current("Ingestion cancelled.");
    }
    router.replace(ingestionHref(slug, null), { scroll: false });
  }, [terminal, status, slug, runId, router, queryClient, onFinished]);

  const view = runProgressView(statusQuery.data?.progress ?? null, statusQuery.data?.post ?? null);
  // From the click until the run really ends — not only while the request is in flight, which is a few
  // milliseconds: after that the status still reads "running" until the worker has stopped and cleaned up.
  const cancelling = cancel.isPending || cancel.isSuccess || status === "cancelling";

  if (statusQuery.isError) {
    return (
      <section
        aria-label="Ingestion progress"
        className="mb-4 rounded-md border border-border bg-card p-3 text-sm"
      >
        Run status unavailable. It may have finished — reload the page to re-check.
      </section>
    );
  }

  return (
    <section
      aria-label="Ingestion progress"
      aria-live="polite"
      className="mb-4 rounded-md border border-border bg-card p-3"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-col items-start gap-2 sm:flex-row sm:items-center sm:gap-3">
          {/* Whose run this is: the logo and name, so the box reads right even when the tab was left open. */}
          <span aria-hidden="true" className="flex h-8 max-w-40 shrink-0 items-center gap-2 overflow-hidden">
            <LogoStrip logos={framework.logos} name={framework.name} max={2} />
          </span>
          <div className="min-w-0">
            <p data-testid="run-framework" className="truncate text-base font-medium">
              {framework.name}
            </p>
            <p className="text-sm">
              <span className="font-medium">{tierLabel(view.tier === "—" ? null : view.tier)}</span>
              {status && !terminal ? (
                <span className={`ml-2 ${cancelling ? "text-warning-text" : "text-muted-foreground"}`}>
                  {cancelling ? "cancelling…" : status}
                </span>
              ) : null}
              {failed ? <span className="ml-2 text-destructive-text">failed</span> : null}
            </p>
          </div>
        </div>
        {!terminal ? (
          <button
            type="button"
            onClick={() => cancel.mutate(runId)}
            disabled={cancelling}
            className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {cancelling ? "Cancelling…" : "Cancel run"}
          </button>
        ) : failed ? (
          <button
            type="button"
            onClick={() => router.replace(ingestionHref(slug, null), { scroll: false })}
            className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground"
          >
            Dismiss
          </button>
        ) : null}
      </div>
      {cancelling && !terminal ? (
        <p data-testid="run-cancelling" className="mt-2 text-sm text-warning-text">
          Cancelling — stopping the run and removing what it had written. This can take a few seconds.
        </p>
      ) : null}
      {cancel.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Could not cancel the run — {apiErrorMessage(cancel.error)}
        </p>
      ) : null}
      {view.percent != null ? (
        <div className="mt-2 h-1 w-full overflow-hidden rounded-sm bg-muted">
          <div className="h-full bg-primary" style={{ width: `${view.percent}%` }} />
        </div>
      ) : null}
      <p className="mt-2 font-mono text-xs text-muted-foreground break-all">
        {progress?.error ? progress.error : view.lastUrl || "starting…"}
      </p>
      {view.counter ? <p className="mt-1 text-xs text-muted-foreground">{view.counter}</p> : null}
      {view.postSummary ? <p className="mt-1 text-xs text-muted-foreground">{view.postSummary}</p> : null}
    </section>
  );
}
