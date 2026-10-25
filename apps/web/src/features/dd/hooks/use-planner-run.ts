"use client";

/** Docs Distiller — planner run controller for one framework. Reload recovery comes from the server's
 * live-run registry (SSE reconnect) or the latest checkpoint (painted statically); progress streams in
 * over SSE and accumulates here, per the `shared/sse` pattern "keep accumulated state in the caller".
 * Mount it with `key={slug}`: a different framework is a fresh controller. */

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getDdPipelineSlugStateQueryKey,
  getDdPlannerDebugGraphThreadIdLlmCountersQueryKey,
  getDdPlannerSlugHealthQueryKey,
} from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { useEventStream } from "@/shared/sse";

import {
  cancelRun,
  fetchGraphState,
  fetchGraphStateRetried,
  fetchPipelineState,
  fetchPlannerActive,
  fetchPlannerTotalMs,
  fetchStageRecent,
  stageEventsUrl,
  startPlanner,
  wipePlanner,
} from "../api";
import { deleteCascadeText } from "../domain/library";
import { plannerLiveText } from "../domain/live-text";
import {
  appendStageEvent,
  generateThreadId,
  IDLE_PILL,
  PLANNER_NODES,
  plannerStartLabel,
  type StageNodeView,
  type StagePillState,
  stageAllImplementedDone,
  stageNodeViews,
  stageProgressLabel,
  terminalPill,
  WORKING_PILL,
} from "../domain/stages";
import { type StageEvent, stageEventSchema } from "../entities";
import { CANCEL_SAFETY_MS, EVENT_LOG_LIMIT } from "../params";
import { useElapsed } from "./use-elapsed";
import { useDdOverlays } from "./use-overlays";
import { stageInfoQuery, useInvalidatePipeline, useStageInfo } from "./use-stage-queries";

export type PlannerRunController = {
  /** The run being followed, or else the latest one — the thread whose LLM usage can be asked for. */
  threadId: string | null;
  running: boolean;
  starting: boolean;
  canceling: boolean;
  pill: StagePillState;
  elapsedMs: number | null;
  views: StageNodeView[];
  liveLines: Record<string, string>;
  /** The newest stream events of this run, oldest first (the node drawer's activity log). */
  events: StageEvent[];
  error: string | null;
  values: Record<string, unknown>;
  next: string[];
  startLabel: "Start" | "Resume";
  progress: string | null;
  start: () => Promise<void>;
  cancel: () => Promise<void>;
  wipe: () => Promise<void>;
};

type PlannerState = {
  threadId: string | null;
  /** The newest thread this controller has seen; unlike `threadId` it survives the run ending. */
  lastThreadId: string | null;
  running: boolean;
  starting: boolean;
  canceling: boolean;
  pill: StagePillState;
  startedMs: number | null;
  values: Record<string, unknown>;
  next: string[];
  liveLines: Record<string, string>;
  events: StageEvent[];
  error: string | null;
  elapsedMs: number | null;
};

const PLANNER_EMPTY: PlannerState = {
  threadId: null,
  lastThreadId: null,
  running: false,
  starting: false,
  canceling: false,
  pill: IDLE_PILL,
  startedMs: null,
  values: {},
  next: [],
  liveLines: {},
  events: [],
  error: null,
  elapsedMs: null,
};

const PLANNER_LOADING: PlannerState = { ...PLANNER_EMPTY, pill: { status: "working", label: "Loading…" } };

const isPlannerTerminal = (event: StageEvent) => event.step === "planner" && event.kind === "terminal";

export function usePlannerRun(
  slug: string,
  { onTerminal }: { onTerminal?: (status: string) => void } = {},
): PlannerRunController {
  const overlays = useDdOverlays();
  const queryClient = useQueryClient();
  const invalidatePipeline = useInvalidatePipeline();
  const [state, setState] = useState<PlannerState>(PLANNER_LOADING);
  const activeThread = useRef<string | null>(null); // lets a late state refresh notice the run ended

  const info = useStageInfo("planner");
  const implemented = useMemo(() => new Set(info?.implemented ?? []), [info]);

  useEffect(() => {
    activeThread.current = state.threadId;
  }, [state.threadId]);

  // Recovery: a live run first (reconnect its stream), else the latest checkpoint painted statically.
  useEffect(() => {
    let alive = true;
    void (async () => {
      const active = await fetchPlannerActive(slug);
      if (!alive) return;
      if (active?.active && active.thread_id) {
        setState({
          ...PLANNER_EMPTY,
          running: true,
          threadId: active.thread_id,
          lastThreadId: active.thread_id,
          startedMs: active.started_ts ? active.started_ts * 1000 : null,
          pill: WORKING_PILL,
        });
        invalidatePipeline();
        return;
      }
      const [totalMs, recent, stageInfo] = await Promise.all([
        fetchPlannerTotalMs(slug),
        fetchStageRecent("planner"),
        queryClient.fetchQuery(stageInfoQuery("planner")).catch(() => null),
      ]);
      if (!alive) return;
      // A run the user started while this was loading wins over the painted checkpoint.
      const settle = (next: PlannerState) => setState((s) => (s.running || s.starting ? s : next));
      const latest = recent.find((item) => item.slug === slug);
      const idle: PlannerState = {
        ...PLANNER_EMPTY,
        lastThreadId: latest?.thread_id ?? null,
        elapsedMs: totalMs > 0 ? totalMs : null,
      };
      const graphState = latest ? await fetchGraphState("planner", latest.thread_id) : null;
      if (!alive) return;
      if (!graphState) {
        settle(idle);
        return;
      }
      const { values, next } = graphState;
      const status = String(values.status ?? "");
      const pill =
        status === "failed" || status === "cancelled"
          ? terminalPill(status)
          : stageAllImplementedDone(PLANNER_NODES, values, new Set(stageInfo?.implemented ?? []))
            ? terminalPill("done")
            : IDLE_PILL;
      settle({ ...idle, values, next, pill });
    })();
    return () => {
      alive = false;
    };
  }, [slug, queryClient, invalidatePipeline]);

  const streamOptions = useMemo(
    () =>
      state.threadId
        ? {
            url: stageEventsUrl("planner", state.threadId),
            headers: { accept: "text/event-stream" },
            parse: (data: unknown): StageEvent => stageEventSchema.parse(data),
            isTerminal: isPlannerTerminal,
          }
        : null,
    [state.threadId],
  );

  useEventStream(streamOptions, (event) => {
    const threadId = state.threadId;
    if (!threadId) return;
    setState((s) => ({ ...s, events: appendStageEvent(s.events, event, EVENT_LOG_LIMIT) }));

    if (isPlannerTerminal(event)) {
      const status = String(event.status ?? "done");
      const wallMs =
        typeof event.total_wall_ms === "number" && event.total_wall_ms > 0 ? event.total_wall_ms : null;
      setState((s) => ({
        ...s,
        running: false,
        threadId: null,
        canceling: false,
        pill: terminalPill(status),
        elapsedMs: wallMs ?? s.elapsedMs,
      }));
      invalidatePipeline();
      void queryClient.invalidateQueries({ queryKey: getDdPipelineSlugStateQueryKey({ path: { slug } }) });
      // The run is over: its final usage and the plan's health are worth one more read.
      void queryClient.invalidateQueries({
        queryKey: getDdPlannerDebugGraphThreadIdLlmCountersQueryKey({ path: { thread_id: threadId } }),
      });
      void queryClient.invalidateQueries({ queryKey: getDdPlannerSlugHealthQueryKey({ path: { slug } }) });
      onTerminal?.(status);
      // One last full snapshot so the figures mirror exactly what the cache holds now.
      void fetchGraphState("planner", threadId).then((final) => {
        if (final) setState((s) => ({ ...s, values: final.values, next: final.next }));
      });
      return;
    }

    if (!event.step) return;
    const step = event.step;
    const text = plannerLiveText(event);
    setState((s) => ({
      ...s,
      liveLines: text ? { ...s.liveLines, [step]: text } : s.liveLines,
      pill: WORKING_PILL,
    }));
    if (event.kind === "done" || event.kind === "sanitized" || event.kind === "llm_progress") {
      const field = PLANNER_NODES.find((node) => node.id === step)?.field ?? null;
      void fetchGraphStateRetried("planner", threadId, field, () => activeThread.current === threadId).then(
        (graphState) => {
          if (graphState) setState((s) => ({ ...s, values: graphState.values, next: graphState.next }));
        },
      );
    }
  });

  const views = useMemo(
    () => stageNodeViews(PLANNER_NODES, state.values, state.next, state.running, implemented),
    [state.values, state.next, state.running, implemented],
  );

  const start = useCallback(async () => {
    if (state.running || state.starting) return;
    setState((s) => ({ ...s, starting: true, error: null }));
    try {
      // Smart resume: an existing thread resumes only its uncached nodes; a fresh slug is a full run.
      const recent = await fetchStageRecent("planner");
      const existing = recent.find((item) => item.slug === slug);
      const threadId = existing?.thread_id ?? generateThreadId(slug);
      setState((s) => ({
        ...s,
        running: true,
        threadId,
        lastThreadId: threadId,
        startedMs: Date.now(),
        pill: WORKING_PILL,
        liveLines: {},
        events: [],
      }));
      invalidatePipeline();
      const outcome = await startPlanner(slug, threadId, existing !== undefined);
      if (outcome.kind === "locked") {
        setState((s) => ({ ...s, running: false, threadId: null, pill: IDLE_PILL }));
        overlays.notice(
          `Planner blocked — ${outcome.message ?? "another pipeline run is in flight."}`,
          "error",
        );
        invalidatePipeline();
      }
    } catch (error) {
      setState((s) => ({
        ...s,
        running: false,
        threadId: null,
        pill: { status: "failed", label: null },
        error: apiErrorMessage(error),
      }));
      invalidatePipeline();
    } finally {
      setState((s) => ({ ...s, starting: false }));
    }
  }, [slug, state.running, state.starting, overlays, invalidatePipeline]);

  const cancel = useCallback(async () => {
    const threadId = state.threadId;
    if (!threadId || state.canceling) return;
    setState((s) => ({ ...s, canceling: true }));
    await cancelRun("planner", threadId);
    // The stream's terminal event is the intended release; if it never lands, free the UI anyway.
    setTimeout(() => {
      setState((s) => (s.canceling ? { ...s, canceling: false } : s));
    }, CANCEL_SAFETY_MS);
  }, [state.threadId, state.canceling]);

  const wipe = useCallback(async () => {
    const pipeline = await fetchPipelineState(slug);
    const confirmed = await overlays.confirm({
      title: "Wipe planner?",
      message: `Deletes this framework's planner cache (embeddings and checkpoints are cleared).${deleteCascadeText(pipeline, "planner")} This cannot be undone.`,
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    if (!(await wipePlanner(slug))) {
      overlays.toast("Wipe failed. Try again.");
      return;
    }
    setState(PLANNER_EMPTY);
    invalidatePipeline();
  }, [slug, overlays, invalidatePipeline]);

  const elapsed = useElapsed(state.running, state.startedMs);

  return {
    running: state.running,
    starting: state.starting,
    canceling: state.canceling,
    pill: state.pill,
    elapsedMs: state.running ? elapsed : state.elapsedMs,
    views,
    liveLines: state.liveLines,
    events: state.events,
    threadId: state.threadId ?? state.lastThreadId,
    error: state.error,
    values: state.values,
    next: state.next,
    startLabel: plannerStartLabel(state.values),
    progress: stageProgressLabel(PLANNER_NODES, views, implemented),
    start,
    cancel,
    wipe,
  };
}
