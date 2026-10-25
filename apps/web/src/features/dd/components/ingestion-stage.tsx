"use client";

/** Ingestion stage: live run progress box + library explorer. Port of
 * ingestion/body.py + manifest.js + polling.js + explorer.js. */

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import {
  getDdIngestionQueryKey,
  getDdIngestionSlugManifestOptions,
  getDdRunsActiveOptions,
  getDdRunsRunIdOptions,
} from "@/shared/api";

import { ingressSummary, isRunTerminal, runProgressView, tierLabel } from "../domain/ingestion";
import { useActiveRun, useCancelRun, useManifest, useRunPoll } from "../hooks/use-dd-data";
import { ingestionHref } from "../params";
import { Explorer } from "./explorer";

export function IngestionStage({ slug, runId }: { slug: string | null; runId: string | null }) {
  const { run } = useActiveRun(true);
  const router = useRouter();

  // Page-reload recovery: with no ?run=, hop to an in-flight run for the
  // same framework (legacy recovery.js).
  useEffect(() => {
    if (runId || !slug || !run || run.slug !== slug) {
      return;
    }
    router.replace(ingestionHref(slug, run.run_id));
  }, [runId, slug, run, router]);

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
  return <IngestionWorkspace slug={slug} runId={runId} />;
}

function IngestionWorkspace({ slug, runId }: { slug: string; runId: string | null }) {
  const manifestQuery = useManifest(slug);
  return (
    <div>
      {manifestQuery.data ? (
        <p className="mb-4 text-sm text-muted-foreground">{ingressSummary(manifestQuery.data)}</p>
      ) : null}
      {runId ? <RunProgressBox slug={slug} runId={runId} /> : null}
      <Explorer slug={slug} manifest={manifestQuery.data} />
    </div>
  );
}

function RunProgressBox({ slug, runId }: { slug: string; runId: string }) {
  const statusQuery = useRunPoll(runId);
  const cancel = useCancelRun();
  const router = useRouter();
  const queryClient = useQueryClient();

  const progress = statusQuery.data?.progress ?? null;
  const terminal = isRunTerminal(progress?.status ?? null);

  // Terminal transition: refresh everything the run touched and drop ?run=
  // from the URL (legacy hides the progress box and re-renders).
  useEffect(() => {
    if (!terminal) {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
    void queryClient.invalidateQueries(getDdIngestionSlugManifestOptions({ path: { slug } }));
    void queryClient.invalidateQueries(getDdRunsActiveOptions());
    void queryClient.invalidateQueries(getDdRunsRunIdOptions({ path: { run_id: runId } }));
    router.replace(ingestionHref(slug, null));
  }, [terminal, slug, runId, router, queryClient]);

  const view = runProgressView(statusQuery.data?.progress ?? null, statusQuery.data?.post ?? null);
  const cancelling = cancel.isPending || progress?.status === "cancelling";

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
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-medium">{tierLabel(view.tier === "—" ? null : view.tier)}</span>
          {progress?.status && !terminal ? (
            <span className="ml-2 text-muted-foreground">{cancelling ? "cancelling…" : progress.status}</span>
          ) : null}
        </p>
        {!terminal ? (
          <button
            type="button"
            onClick={() => cancel.mutate(runId)}
            disabled={cancel.isPending || cancelling}
            className="min-h-9 rounded-sm border border-border px-3 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {cancelling ? "Cancelling…" : "Cancel run"}
          </button>
        ) : null}
      </div>
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
