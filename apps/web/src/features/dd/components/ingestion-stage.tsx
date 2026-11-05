"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getDdIngestionQueryKey,
  getDdIngestionSlugManifestOptions,
  getDdIngestionSlugPagesIdxQueryKey,
  getDdRunsActiveOptions,
  getDdRunsRunIdOptions,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { ingressSummary, isRunTerminal, runFramework, runToAdopt } from "../domain/ingestion";
import type { Manifest } from "../entities";
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
import { IngestionPipeline } from "./ingestion-pipeline";

type IngestionView = "pipeline" | "materials";

export function IngestionStage({ slug, runId }: { slug: string | null; runId: string | null }) {
  const { runs } = useActiveRun(true);
  const router = useRouter();
  const finished = useRef<Set<string>>(new Set());
  const rememberFinished = useCallback((id: string) => {
    finished.current.add(id);
  }, []);

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
  return <IngestionWorkspace key={slug} slug={slug} runId={runId} onRunFinished={rememberFinished} />;
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
  const [activeView, setActiveView] = useState<IngestionView>("pipeline");
  const manifestQuery = useManifest(slug);
  const statusQuery = useRunPoll(runId);
  const cancel = useCancelRun();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { notice } = useDdOverlays();
  const noticeRef = useRef(notice);
  const catalog = useCatalog();
  const library = useLibrary();
  const framework = runFramework(slug, catalog.data, library.data);

  useEffect(() => {
    noticeRef.current = notice;
  });

  const progress = statusQuery.data?.progress ?? null;
  const status = progress?.status ?? null;
  const terminal = isRunTerminal(status);

  useEffect(() => {
    if (!runId || !terminal) return;
    onRunFinished(runId);
    const keepGoing = { cancelRefetch: false };
    void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() }, keepGoing);
    void queryClient.invalidateQueries(getDdIngestionSlugManifestOptions({ path: { slug } }), keepGoing);
    void queryClient.invalidateQueries(getDdRunsActiveOptions(), keepGoing);
    void queryClient.invalidateQueries(getDdRunsRunIdOptions({ path: { run_id: runId } }), keepGoing);
    void queryClient.invalidateQueries({ queryKey: ["dd", "ingestion", "usage", runId] }, keepGoing);
    for (const entry of statusQuery.data?.manifest ?? []) {
      void queryClient.invalidateQueries(
        { queryKey: getDdIngestionSlugPagesIdxQueryKey({ path: { slug, idx: entry.idx } }) },
        keepGoing,
      );
    }
    if (status === "cancelled") noticeRef.current("Ingestion cancelled.");
  }, [runId, terminal, status, slug, queryClient, onRunFinished, statusQuery.data?.manifest]);

  const liveEntries = statusQuery.data?.manifest;
  const showLiveManifest = runId !== null && liveEntries != null;
  const activeManifest: Manifest | null = showLiveManifest
    ? {
        framework_name: framework.name,
        framework_slug: slug,
        entries: liveEntries ?? [],
        total_bytes: (liveEntries ?? []).reduce((total, entry) => total + (entry.bytes ?? 0), 0),
        tier_kind: progress?.stages?.extraction?.tier ?? null,
        ingested_at: null,
      }
    : manifestQuery.data;
  const explorerRunId = showLiveManifest ? runId : null;
  const cancelling = cancel.isPending || cancel.isSuccess || status === "cancelling";

  return (
    <div>
      {activeManifest ? (
        <p className="mb-4 text-sm text-muted-foreground">{ingressSummary(activeManifest)}</p>
      ) : null}
      <div role="tablist" aria-label="Ingestion views" className="border-b border-border">
        {(
          [
            ["pipeline", "Pipeline"],
            ["materials", "Materials"],
          ] as const
        ).map(([viewId, label]) => (
          <button
            key={viewId}
            id={`dd-ingestion-${viewId}-tab`}
            type="button"
            role="tab"
            aria-selected={activeView === viewId}
            aria-controls={`dd-ingestion-${viewId}-panel`}
            tabIndex={activeView === viewId ? 0 : -1}
            onClick={() => setActiveView(viewId)}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                const nextView = viewId === "pipeline" ? "materials" : "pipeline";
                setActiveView(nextView);
                document.getElementById(`dd-ingestion-${nextView}-tab`)?.focus();
              }
            }}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              activeView === viewId
                ? "border-primary font-medium text-primary-text"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id="dd-ingestion-pipeline-panel"
        aria-labelledby="dd-ingestion-pipeline-tab"
        hidden={activeView !== "pipeline"}
        className="mt-4"
      >
        <IngestionPipeline
          runId={runId}
          progress={progress}
          onCancel={runId && !terminal ? () => cancel.mutate(runId) : undefined}
          onDismiss={
            runId && terminal ? () => router.replace(ingestionHref(slug, null), { scroll: false }) : undefined
          }
          cancelling={cancelling}
          cancelError={cancel.isError ? apiErrorMessage(cancel.error) : null}
          statusUnavailable={runId !== null && statusQuery.isError}
        />
      </div>

      <div
        role="tabpanel"
        id="dd-ingestion-materials-panel"
        aria-labelledby="dd-ingestion-materials-tab"
        hidden={activeView !== "materials"}
        className="mt-4"
      >
        <Explorer slug={slug} manifest={activeManifest} runId={explorerRunId} />
      </div>
    </div>
  );
}
