"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";

import {
  getDdPagesQueryKey,
  getDdPagesRunsActiveOptions,
  getDdPagesRunsRunIdOptions,
  getDdPagesSlugManifestOptions,
  getDdPagesSlugPagesIdxQueryKey,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { frameworkInfo, isRunTerminal, runFramework, runToAdopt, tierLabel } from "../domain/pages";
import { phaseStatusText } from "../domain/extraction-progress";
import { deleteCascadeText } from "../domain/library";
import type { Manifest, RunStatus } from "../entities";
import {
  useActiveRun,
  useCancelRun,
  useCatalog,
  useDeleteLibrary,
  useLibrary,
  useManifest,
  useRunPoll,
  useStartRun,
} from "../hooks/use-dd-data";
import { useDdOverlays } from "../hooks/use-overlays";
import { pagesHref } from "../params";
import { ExtractionProgress } from "./extraction-progress";
import { MaterialsView } from "./materials-view";
import { PageSelectionUsageControl } from "./page-selection-usage";
import { StageBar } from "./stage-bar";

type ActiveExtractionRun = ReturnType<typeof useActiveRun>["runs"][number];

function completedProgress(pageCount: number): NonNullable<RunStatus["progress"]> {
  return {
    tier: "finalize",
    status: "done",
    current: pageCount,
    total: pageCount,
    last_url: "",
    error: null,
    updated_at: null,
    stages: {
      extraction: {
        status: "done",
        tier: null,
        current: pageCount,
        total: pageCount,
        last_url: "",
        error: null,
      },
      selection: {
        status: "done",
        current: pageCount,
        total: pageCount,
        error: null,
      },
    },
  };
}

export function PagesStage({ slug, runId }: { slug: string | null; runId: string | null }) {
  const { runs } = useActiveRun(true);
  const router = useRouter();
  const activeRun = runs.find((candidate) => candidate.slug === slug);
  const finished = useRef<Set<string>>(new Set());
  const rememberFinished = useCallback((id: string) => {
    finished.current.add(id);
  }, []);

  useEffect(() => {
    const adopt = runToAdopt({
      slug,
      urlRunId: runId,
      active: activeRun,
      finished: finished.current,
    });
    if (adopt && slug) {
      router.replace(pagesHref(slug, adopt), { scroll: false });
    }
  }, [runId, slug, activeRun, router]);

  if (!slug) {
    return (
      <>
        <StageBar stage="pages" slug={null} />
        <div className="rounded-md border border-border bg-card p-8 text-center">
          <h2 className="text-base font-medium">Choose documentation</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Choose a framework from Catalog to view or fetch its pages.
          </p>
        </div>
      </>
    );
  }
  return (
    <PagesWorkspace
      key={slug}
      slug={slug}
      runId={runId}
      activeRun={activeRun}
      onRunFinished={rememberFinished}
    />
  );
}

function PagesWorkspace({
  slug,
  runId,
  activeRun,
  onRunFinished,
}: {
  slug: string;
  runId: string | null;
  activeRun: ActiveExtractionRun | undefined;
  onRunFinished: (runId: string) => void;
}) {
  const manifestQuery = useManifest(slug);
  const effectiveRunId = runId ?? manifestQuery.data?.run_id ?? null;
  const selectionRunId = effectiveRunId;
  // A run named in the address is in flight (or just ended); the run saved with the library is finished, and its status is read once.
  const statusQuery = useRunPoll(effectiveRunId, runId !== null);
  const cancel = useCancelRun();
  const start = useStartRun();
  const wipe = useDeleteLibrary();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { notice, toast, confirm } = useDdOverlays();
  const noticeRef = useRef(notice);
  const catalog = useCatalog();
  const library = useLibrary();
  const framework = runFramework(slug, catalog.data, library.data);
  const ingested = (library.data ?? []).some((item) => item.slug === slug);

  useEffect(() => {
    noticeRef.current = notice;
  });

  const progress =
    statusQuery.data?.progress ??
    (statusQuery.isError && effectiveRunId === manifestQuery.data?.run_id && manifestQuery.data
      ? completedProgress(manifestQuery.data.entries.length)
      : null);
  const status = progress?.status ?? null;
  const terminal = isRunTerminal(status);
  const extraction = progress?.stages?.extraction ?? null;
  const selection = progress?.stages?.selection ?? null;
  const statusUnavailable = effectiveRunId !== null && statusQuery.isError;

  // A Pages URL can outlive its run. Follow a newer active run, or the latest published manifest
  // after a newer run finishes, so progress and usage stay aligned with the current page set.
  const activeRunId = activeRun?.run_id ?? null;
  const activeRunIsLive = activeRun !== undefined && !isRunTerminal(activeRun.progress?.status);
  const savedRunId = manifestQuery.data?.run_id ?? null;
  const savedRunIsNewer =
    savedRunId !== null &&
    savedRunId !== runId &&
    (statusUnavailable || (manifestQuery.data?.ingested_at ?? 0) > (progress?.updated_at ?? 0));
  const preferredRunId = activeRunIsLive ? activeRunId : savedRunIsNewer ? savedRunId : null;
  const shouldFollowLatestRun =
    runId !== null && preferredRunId !== null && preferredRunId !== runId && (terminal || statusUnavailable);

  useEffect(() => {
    if (!shouldFollowLatestRun || !preferredRunId) return;
    router.replace(pagesHref(slug, preferredRunId), { scroll: false });
  }, [preferredRunId, router, shouldFollowLatestRun, slug]);

  useEffect(() => {
    if (!runId || !terminal) return;
    onRunFinished(runId);
    const keepGoing = { cancelRefetch: false };
    void queryClient.invalidateQueries({ queryKey: getDdPagesQueryKey() }, keepGoing);
    void queryClient.invalidateQueries(getDdPagesSlugManifestOptions({ path: { slug } }), keepGoing);
    void queryClient.invalidateQueries(getDdPagesRunsActiveOptions(), keepGoing);
    void queryClient.invalidateQueries(getDdPagesRunsRunIdOptions({ path: { run_id: runId } }), keepGoing);
    void queryClient.invalidateQueries({ queryKey: ["dd", "pages", "usage", runId] }, keepGoing);
    for (const entry of statusQuery.data?.manifest ?? []) {
      void queryClient.invalidateQueries(
        { queryKey: getDdPagesSlugPagesIdxQueryKey({ path: { slug, idx: entry.idx } }) },
        keepGoing,
      );
    }
    if (status === "cancelled" && !shouldFollowLatestRun) {
      noticeRef.current("Documentation extraction cancelled.");
    }
  }, [
    runId,
    terminal,
    status,
    slug,
    queryClient,
    onRunFinished,
    statusQuery.data?.manifest,
    shouldFollowLatestRun,
  ]);

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
  const hasMaterials = ingested || (activeManifest?.entries.length ?? 0) > 0;
  const explorerRunId = showLiveManifest ? runId : null;
  // A cancel that was sent for an earlier run says nothing about this one.
  const cancelRequested =
    status === "cancelling" ||
    ((cancel.isPending || cancel.isSuccess) && cancel.variables === effectiveRunId);
  const extractionActive =
    effectiveRunId !== null && !terminal && !statusUnavailable && extraction?.status === "running";
  const selectionActive =
    effectiveRunId !== null && !terminal && !statusUnavailable && selection?.status === "running";
  const pipelineActive = extractionActive || selectionActive;
  const extractionStopping = pipelineActive && cancelRequested;
  const extractionCurrent =
    effectiveRunId !== null ? (extraction?.current ?? 0) : (manifestQuery.data?.entries.length ?? 0);
  const extractionTotal =
    effectiveRunId !== null ? (extraction?.total ?? 0) : (manifestQuery.data?.entries.length ?? 0);
  const selectionEntries = activeManifest?.entries ?? [];
  const selectionRecorded = selectionEntries.filter((entry) => entry.page_selection?.status).length;
  const selectionRecordedComplete =
    selectionEntries.length > 0 && selectionRecorded === selectionEntries.length;
  const selectionStatusText = selection?.status
    ? selection.status === "running"
      ? "Running"
      : selection.status === "done"
        ? "Complete"
        : phaseStatusText(selection.status, status)
    : selectionRecordedComplete
      ? "Complete"
      : effectiveRunId !== null && !terminal && !statusUnavailable
        ? "Waiting"
        : ingested
          ? "Not recorded"
          : "Waiting";
  const selectionCurrent = selection?.current ?? selectionRecorded;
  const selectionTotal = selection?.total ?? (terminal || statusUnavailable ? selectionEntries.length : 0);
  const extractionStatusText = (() => {
    if (extractionStopping) return "Stopping";
    if (effectiveRunId !== null) return phaseStatusText(extraction?.status, status);
    return ingested ? "Complete" : "Not started";
  })();
  const extractionDone = effectiveRunId !== null ? extraction?.status === "done" : ingested;
  const retryable = status === "failed" || status === "cancelled";
  const extractionCancelError =
    cancel.isError && cancel.variables === effectiveRunId
      ? `Could not cancel the run — ${apiErrorMessage(cancel.error)}`
      : null;
  const runError = extraction?.error ?? selection?.error ?? progress?.error ?? null;

  const startExtraction = () =>
    start.mutate(
      { slug },
      {
        onSuccess: (outcome) => {
          if (outcome.kind === "queued") {
            notice(`Fetching documentation for ${framework.name}…`);
            router.replace(pagesHref(slug, outcome.runId ?? null), { scroll: false });
          } else if (outcome.kind === "locked") {
            toast(outcome.message ?? "A run is already active for this framework.");
          } else {
            notice("Already up to date.");
          }
        },
        onError: (error) => toast(apiErrorMessage(error)),
      },
    );

  const wipePages = async () => {
    const confirmed = await confirm({
      title: "Wipe saved pages?",
      message: `Deletes ${framework.name}'s saved pages and page-selection results permanently.${deleteCascadeText({ planner: true, synth: true }, "")} The framework stays in the catalog so you can fetch its pages again.`,
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    wipe.mutate(slug, {
      onSuccess: () => {
        notice(`Wiped ${framework.name}'s saved pages.`);
        router.replace(pagesHref(slug, null), { scroll: false });
      },
      onError: (error) => toast(apiErrorMessage(error)),
    });
  };

  return (
    <div>
      {!hasMaterials ? (
        <StageBar stage="pages" slug={slug} info={frameworkInfo(activeManifest) || framework.name} />
      ) : null}
      <div className="mb-4">
        <ExtractionProgress
          statusText={extractionStatusText}
          current={extractionCurrent}
          total={extractionTotal}
          tierText={extraction?.tier ? tierLabel(extraction.tier) : null}
          lastUrl={extraction?.last_url ?? null}
          successful={extractionDone && !extraction?.error && (!selection || selection.status === "done")}
          live={effectiveRunId !== null && !terminal && !statusUnavailable}
          extracting={pipelineActive}
          ingested={ingested}
          retryable={retryable}
          stopping={extractionStopping}
          startPending={start.isPending}
          wipePending={wipe.isPending}
          statusUnavailable={statusUnavailable}
          cancelError={extractionCancelError}
          errorText={runError}
          finishedText={
            terminal
              ? `Documentation extraction ${phaseStatusText(extraction?.status, status).toLowerCase()}.`
              : null
          }
          selectionStatusText={selectionStatusText}
          selectionCurrent={selectionCurrent}
          selectionTotal={selectionTotal}
          selectionActive={selectionActive}
          usageControl={
            <PageSelectionUsageControl
              runId={selectionRunId}
              live={runId !== null && selectionActive}
              assessed={selectionCurrent}
              total={selectionTotal}
            />
          }
          onStart={startExtraction}
          onStop={() => {
            if (effectiveRunId) cancel.mutate(effectiveRunId);
          }}
          onWipe={() => void wipePages()}
        />
      </div>
      {hasMaterials ? (
        <MaterialsView
          slug={slug}
          info={frameworkInfo(activeManifest) || framework.name}
          controlsInNavbar
          manifest={activeManifest}
          runId={explorerRunId}
          selectionPending={effectiveRunId !== null && !terminal && !statusUnavailable}
          active
          loadError={ingested && manifestQuery.isError && !manifestQuery.data && !showLiveManifest}
        />
      ) : (
        <section
          aria-label="Pages"
          className="rounded-md border border-border bg-card p-8 text-center text-sm text-muted-foreground"
        >
          {effectiveRunId !== null && !terminal && !statusUnavailable
            ? "AI-approved pages will appear here as extraction continues."
            : "No source pages yet. Start documentation extraction above."}
        </section>
      )}
    </div>
  );
}
