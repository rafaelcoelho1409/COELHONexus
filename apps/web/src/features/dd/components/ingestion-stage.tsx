"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef } from "react";

import {
  getDdIngestionQueryKey,
  getDdIngestionRunsActiveOptions,
  getDdIngestionRunsRunIdOptions,
  getDdIngestionSlugManifestOptions,
  getDdIngestionSlugPagesIdxQueryKey,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { frameworkInfo, isRunTerminal, runFramework, runToAdopt, tierLabel } from "../domain/ingestion";
import { phaseStatusText } from "../domain/ingestion-progress";
import { deleteCascadeText } from "../domain/library";
import type { Manifest, RunStatus } from "../entities";
import {
  useActiveRun,
  useCancelRun,
  useCatalog,
  useDeleteLibrary,
  useLibrary,
  useManifest,
  useResumeAgenticRun,
  useReplayAgenticRunNode,
  useRunPoll,
  useStartAgenticRun,
  useStartRun,
  useWipeAgenticRun,
} from "../hooks/use-dd-data";
import { useDdOverlays } from "../hooks/use-overlays";
import { ingestionHref, pagesHref } from "../params";
import { IngestionPipeline } from "./ingestion-pipeline";
import { ExtractionProgress } from "./ingestion-progress";
import { MaterialsView } from "./materials-view";
import { StageBar } from "./stage-bar";

type IngestionView = "pipeline" | "materials";

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
      agentic: {
        status: "done",
        current: pageCount,
        total: pageCount,
        current_node: null,
        node_status: {
          audit_pages: "done",
          normalize_pages: "done",
          publish: "done",
        },
        node_progress: {
          audit_pages: { current: pageCount, total: pageCount },
          normalize_pages: { current: pageCount, total: pageCount },
        },
        error: null,
      },
    },
  };
}

export function IngestionStage({
  slug,
  runId,
  view = "pipeline",
}: {
  slug: string | null;
  runId: string | null;
  view?: IngestionView;
}) {
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
    if (adopt && slug) {
      router.replace(view === "materials" ? pagesHref(slug, adopt) : ingestionHref(slug, adopt), {
        scroll: false,
      });
    }
  }, [runId, slug, runs, router, view]);

  if (!slug) {
    return (
      <>
        <StageBar stage={view === "pipeline" ? "ingestion" : "pages"} slug={null} />
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
    <IngestionWorkspace key={slug} slug={slug} runId={runId} view={view} onRunFinished={rememberFinished} />
  );
}

function IngestionWorkspace({
  slug,
  runId,
  view,
  onRunFinished,
}: {
  slug: string;
  runId: string | null;
  view: IngestionView;
  onRunFinished: (runId: string) => void;
}) {
  const manifestQuery = useManifest(slug);
  const effectiveRunId = runId ?? manifestQuery.data?.agentic_run_id ?? null;
  // A run named in the address is in flight (or just ended); the run saved with the library is finished, and its status is read once.
  const statusQuery = useRunPoll(effectiveRunId, runId !== null);
  const cancel = useCancelRun();
  const start = useStartRun();
  const startAgentic = useStartAgenticRun();
  const resumeAgentic = useResumeAgenticRun();
  const replayAgenticNode = useReplayAgenticRunNode();
  const wipeAgentic = useWipeAgenticRun();
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
    (statusQuery.isError && effectiveRunId === manifestQuery.data?.agentic_run_id && manifestQuery.data
      ? completedProgress(manifestQuery.data.entries.length)
      : null);
  const status = progress?.status ?? null;
  const agentic = progress?.stages?.agentic ?? null;
  const terminal = isRunTerminal(status);
  const extraction = progress?.stages?.extraction ?? null;

  useEffect(() => {
    if (!runId || !terminal) return;
    onRunFinished(runId);
    const keepGoing = { cancelRefetch: false };
    void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() }, keepGoing);
    void queryClient.invalidateQueries(getDdIngestionSlugManifestOptions({ path: { slug } }), keepGoing);
    void queryClient.invalidateQueries(getDdIngestionRunsActiveOptions(), keepGoing);
    void queryClient.invalidateQueries(
      getDdIngestionRunsRunIdOptions({ path: { run_id: runId } }),
      keepGoing,
    );
    void queryClient.invalidateQueries({ queryKey: ["dd", "ingestion", "usage", runId] }, keepGoing);
    for (const entry of statusQuery.data?.manifest ?? []) {
      void queryClient.invalidateQueries(
        { queryKey: getDdIngestionSlugPagesIdxQueryKey({ path: { slug, idx: entry.idx } }) },
        keepGoing,
      );
    }
    if (status === "cancelled") noticeRef.current("Documentation extraction cancelled.");
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
        agentic_run_id: manifestQuery.data?.agentic_run_id ?? null,
      }
    : manifestQuery.data;
  const hasMaterials = ingested || (activeManifest?.entries.length ?? 0) > 0;
  const explorerRunId = showLiveManifest ? runId : null;
  // A cancel that was sent for an earlier run says nothing about this one.
  const cancelRequested =
    status === "cancelling" ||
    ((cancel.isPending || cancel.isSuccess) && cancel.variables === effectiveRunId);
  const statusUnavailable = effectiveRunId !== null && statusQuery.isError;
  const extractionActive =
    effectiveRunId !== null && !terminal && !statusUnavailable && extraction?.status === "running";
  const extractionStopping = extractionActive && cancelRequested;
  const extractionCurrent =
    effectiveRunId !== null ? (extraction?.current ?? 0) : (manifestQuery.data?.entries.length ?? 0);
  const extractionTotal =
    effectiveRunId !== null ? (extraction?.total ?? 0) : (manifestQuery.data?.entries.length ?? 0);
  const extractionStatusText =
    effectiveRunId !== null
      ? extractionStopping
        ? "Stopping"
        : phaseStatusText(extraction?.status, status)
      : ingested
        ? "Complete"
        : "Not started";
  const extractionDone = effectiveRunId !== null ? extraction?.status === "done" : ingested;
  const retryable = status === "failed" || status === "cancelled";
  const extractionCancelError =
    cancel.isError && cancel.variables === effectiveRunId
      ? `Could not cancel the run — ${apiErrorMessage(cancel.error)}`
      : null;

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

  const handleAgenticStartOutcome = (outcome: {
    kind: "cached" | "queued" | "locked";
    runId?: string;
    message?: string;
  }) => {
    if (outcome.kind === "queued") {
      notice(`Running Agentic AI for ${framework.name}…`);
      router.replace(ingestionHref(slug, outcome.runId ?? null), { scroll: false });
    } else {
      toast(outcome.message ?? "Another ingestion run is active for this framework.");
    }
  };

  const startAgenticAI = () =>
    startAgentic.mutate(slug, {
      onSuccess: handleAgenticStartOutcome,
      onError: (error) => toast(apiErrorMessage(error)),
    });

  const resumeAgenticAI = () => {
    if (!effectiveRunId) return;
    resumeAgentic.mutate(
      { slug, runId: effectiveRunId },
      {
        onSuccess: handleAgenticStartOutcome,
        onError: (error) => toast(apiErrorMessage(error)),
      },
    );
  };

  const replayAgenticAIFromNode = async (nodeId: string) => {
    const agenticRunId = activeManifest?.agentic_run_id;
    if (!agenticRunId || agentic?.status === "queued" || agentic?.status === "running") return;
    const confirmed = await confirm({
      title: "Replay Agentic AI from this node?",
      message: `Clears saved output from “${nodeId}” and later nodes, then reruns them. Earlier work is preserved. This cannot be undone.`,
      confirmLabel: "Replay node",
    });
    if (!confirmed) return;
    replayAgenticNode.mutate(
      { slug, runId: agenticRunId, nodeId },
      {
        onSuccess: () => notice(`Agentic AI replay queued from ${nodeId}.`),
        onError: (error) => toast(apiErrorMessage(error)),
      },
    );
  };

  const stopAgenticAI = () => {
    if (effectiveRunId) cancel.mutate(effectiveRunId);
  };

  const wipeAgenticAI = async () => {
    if (!effectiveRunId) return;
    const confirmed = await confirm({
      title: "Wipe Agentic AI results?",
      message: `Deletes ${framework.name}'s AI assessments, processed pages, usage counters, and checkpoints. The extracted documentation stays available.`,
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    wipeAgentic.mutate(
      { slug, runId: effectiveRunId },
      {
        onSuccess: () => {
          notice(`Wiped ${framework.name}'s Agentic AI results.`);
          router.replace(pagesHref(slug, null), { scroll: false });
          void queryClient.invalidateQueries(getDdIngestionSlugManifestOptions({ path: { slug } }));
          void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
        },
        onError: (error) => toast(apiErrorMessage(error)),
      },
    );
  };

  const wipeIngestion = async () => {
    const confirmed = await confirm({
      title: "Wipe saved pages?",
      message: `Deletes ${framework.name}'s saved pages and Agentic AI results permanently.${deleteCascadeText({ planner: true, synth: true }, "")} The framework stays in the catalog so you can fetch its pages again.`,
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
      {view === "pipeline" || !hasMaterials ? (
        <StageBar
          stage={view === "pipeline" ? "ingestion" : "pages"}
          slug={slug}
          info={frameworkInfo(activeManifest) || framework.name}
        />
      ) : null}
      {view === "pipeline" ? (
        <section aria-label="Agents">
          <IngestionPipeline
            runId={effectiveRunId}
            agenticRunId={activeManifest?.agentic_run_id ?? null}
            progress={progress}
            ingested={ingested}
            ingestedPages={manifestQuery.data?.entries.length ?? 0}
            startPending={start.isPending}
            wipePending={wipe.isPending}
            agenticActionPending={startAgentic.isPending || resumeAgentic.isPending}
            agenticCancelPending={cancel.isPending && cancel.variables === effectiveRunId}
            agenticWipePending={wipeAgentic.isPending}
            cancelRequested={cancelRequested}
            cancelError={
              cancel.isError ? `Could not cancel the run — ${apiErrorMessage(cancel.error)}` : null
            }
            statusUnavailable={statusUnavailable}
            onStart={startExtraction}
            onStop={() => {
              if (runId) cancel.mutate(runId);
            }}
            onStartAgentic={startAgenticAI}
            onResumeAgentic={resumeAgenticAI}
            onStopAgentic={stopAgenticAI}
            onWipeAgentic={() => void wipeAgenticAI()}
            onPlayAgenticNode={(nodeId) => void replayAgenticAIFromNode(nodeId)}
            onWipe={() => void wipeIngestion()}
          />
        </section>
      ) : null}

      {view === "materials" ? (
        <>
          <div className="mb-4">
            <ExtractionProgress
              statusText={extractionStatusText}
              current={extractionCurrent}
              total={extractionTotal}
              tierText={extraction?.tier ? tierLabel(extraction.tier) : null}
              lastUrl={extraction?.last_url ?? null}
              successful={extractionDone && !extraction?.error}
              live={effectiveRunId !== null && !terminal && !statusUnavailable}
              extracting={extractionActive}
              ingested={ingested}
              retryable={retryable}
              stopping={extractionStopping}
              startPending={start.isPending}
              wipePending={wipe.isPending}
              statusUnavailable={statusUnavailable}
              cancelError={extractionCancelError}
              finishedText={
                terminal
                  ? `Documentation extraction ${phaseStatusText(extraction?.status, status).toLowerCase()}.`
                  : null
              }
              onStart={startExtraction}
              onStop={() => {
                if (effectiveRunId) cancel.mutate(effectiveRunId);
              }}
              onWipe={() => void wipeIngestion()}
            />
          </div>
          {hasMaterials ? (
            <MaterialsView
              slug={slug}
              info={frameworkInfo(activeManifest) || framework.name}
              controlsInNavbar
              manifest={activeManifest}
              runId={explorerRunId}
              active
              loadError={ingested && manifestQuery.isError && !manifestQuery.data && !showLiveManifest}
            />
          ) : (
            <section
              aria-label="Pages"
              className="rounded-md border border-border bg-card p-8 text-center text-sm text-muted-foreground"
            >
              {effectiveRunId !== null && !terminal && !statusUnavailable
                ? "Pages will appear here as extraction publishes them."
                : "No source pages yet. Start documentation extraction above."}
            </section>
          )}
        </>
      ) : null}
    </div>
  );
}
