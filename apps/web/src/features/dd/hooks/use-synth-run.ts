"use client";

/** Docs Distiller — synth run controller for one framework: the study orchestrator (all chapters) plus
 * single-chapter runs, with a "canvas" showing the node graph of one chapter thread (live, or an idle
 * peek at a rendered chapter). Two SSE streams feed it: the study thread and the live chapter thread.
 * Mount it with `key={slug}`: a different framework is a fresh controller.
 *
 * Several stream events can land before React re-renders, so every decision that depends on the
 * current state is made inside a functional `setState`, never from a render-time closure. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { useEventStream } from "@/shared/sse";

import {
  cancelRun,
  fetchGraphState,
  fetchGraphStateRetried,
  fetchPipelineState,
  fetchSynthActive,
  fetchSynthChapters,
  stageEventsUrl,
  startSynthChapter,
  startSynthStudy,
  wipeSynth,
  wipeSynthChapter,
} from "../api";
import {
  type ChapterCell,
  cellsFromIds,
  cellsFromPlan,
  chapterCounter,
  harmonizeDoneLabel,
  stringList,
  synthStartLabel,
  toChapterStatus,
} from "../domain/chapters";
import { deleteCascadeText } from "../domain/library";
import { synthLiveText } from "../domain/live-text";
import {
  appendStageEvent,
  IDLE_PILL,
  type StageNodeView,
  type StagePillState,
  SYNTH_NODES,
  stageNodeViews,
  stageProgressLabel,
  terminalPill,
  WORKING_PILL,
} from "../domain/stages";
import { type StageEvent, stageEventSchema } from "../entities";
import { CANCEL_SAFETY_MS, EVENT_LOG_LIMIT } from "../params";
import { useElapsed } from "./use-elapsed";
import { useDdOverlays } from "./use-overlays";
import { useInvalidatePipeline, useStageInfo } from "./use-stage-queries";

type HarmonizeState = { status: "idle" | "running" | "skipped" | "done"; label: string };

const IDLE_HARMONIZE: HarmonizeState = { status: "idle", label: "" };

export type SynthRunController = {
  running: boolean;
  starting: boolean;
  canceling: boolean;
  pill: StagePillState;
  elapsedMs: number | null;
  cells: ChapterCell[];
  counter: string;
  startLabel: "Start" | "Resume";
  values: Record<string, unknown>;
  liveLines: Record<string, string>;
  /** The newest stream events of the attached chapter run, oldest first (the node drawer's activity log). */
  events: StageEvent[];
  harmonize: HarmonizeState;
  error: string | null;
  viewChapterId: string | null;
  /** The chapter thread the node board shows (live, or a rendered chapter being read). */
  canvasThreadId: string | null;
  views: StageNodeView[];
  next: string[];
  progress: string | null;
  start: () => Promise<void>;
  cancel: () => Promise<void>;
  wipe: () => Promise<void>;
  runChapter: (chapterId: string) => Promise<void>;
  wipeChapter: (chapterId: string) => Promise<void>;
  selectChapter: (chapterId: string) => Promise<void>;
};

/** The chapter thread the node graph is attached to. */
type Canvas = { threadId: string; chapterId: string | null; live: boolean };

type SynthState = {
  running: boolean;
  starting: boolean;
  canceling: boolean;
  studyThreadId: string | null;
  canvas: Canvas | null;
  canvasStartedMs: number | null;
  pill: StagePillState;
  studyStartedMs: number | null;
  cells: ChapterCell[];
  values: Record<string, unknown>;
  next: string[];
  liveLines: Record<string, string>;
  events: StageEvent[];
  error: string | null;
  elapsedMs: number | null;
  harmonize: HarmonizeState;
};

const SYNTH_EMPTY: SynthState = {
  running: false,
  starting: false,
  canceling: false,
  studyThreadId: null,
  canvas: null,
  canvasStartedMs: null,
  pill: IDLE_PILL,
  studyStartedMs: null,
  cells: [],
  values: {},
  next: [],
  liveLines: {},
  events: [],
  error: null,
  elapsedMs: null,
  harmonize: IDLE_HARMONIZE,
};

const SYNTH_LOADING: SynthState = { ...SYNTH_EMPTY, pill: { status: "working", label: "Loading…" } };

/** The state fields that describe the attached chapter thread — spread to detach it. */
const DETACHED: Pick<SynthState, "canvas" | "canvasStartedMs" | "values" | "next" | "liveLines" | "events"> =
  {
    canvas: null,
    canvasStartedMs: null,
    values: {},
    next: [],
    liveLines: {},
    events: [],
  };

const isSynthTerminal = (event: StageEvent) => event.step === "synth" && event.kind === "terminal";

const numberOr = (value: unknown, fallback: number): number => (typeof value === "number" ? value : fallback);

const BLOCKED = "another pipeline run is in flight.";

export function useSynthRun(slug: string): SynthRunController {
  const overlays = useDdOverlays();
  const invalidatePipeline = useInvalidatePipeline();
  const [state, setState] = useState<SynthState>(SYNTH_LOADING);
  const attachedThread = useRef<string | null>(null); // lets a late state refresh notice the chapter ended

  useEffect(() => {
    attachedThread.current = state.canvas?.threadId ?? null;
  }, [state.canvas?.threadId]);

  const info = useStageInfo("synth");
  const implemented = useMemo(() => new Set(info?.implemented ?? []), [info]);

  const setCells = useCallback((update: (cells: ChapterCell[]) => ChapterCell[]) => {
    setState((s) => ({ ...s, cells: update(s.cells) }));
  }, []);

  /** Chapter list with durable render status; returns the cells so recovery can match a thread to one. */
  const hydratePlan = useCallback(async (): Promise<ChapterCell[]> => {
    const data = await fetchSynthChapters(slug);
    if (!data || data.chapters.length === 0) return [];
    const cells = cellsFromPlan(data);
    const totalMs = data.study_total_wall_ms > 0 ? data.study_total_wall_ms : null;
    // A book with every chapter written is "Done", not "Idle" — the same word the planner shows for its plan.
    const finished = cells.every((cell) => cell.status === "done");
    setState((s) => ({
      ...s,
      cells,
      pill: s.running ? s.pill : finished ? terminalPill("done") : IDLE_PILL,
      elapsedMs: s.elapsedMs ?? totalMs,
    }));
    return cells;
  }, [slug]);

  const paintCanvas = useCallback(async (threadId: string) => {
    const graphState = await fetchGraphState("synth", threadId);
    if (!graphState) return;
    setState((s) =>
      s.canvas?.threadId === threadId ? { ...s, values: graphState.values, next: graphState.next } : s,
    );
  }, []);

  const attachCanvas = useCallback(
    (threadId: string, chapterId: string | null, live: boolean) => {
      const startedMs = Date.now();
      setState((s) => ({
        ...s,
        canvas: { threadId, chapterId, live },
        canvasStartedMs: live ? startedMs : s.canvasStartedMs,
        running: s.running || live,
        values: {},
        next: [],
        liveLines: {},
        events: [],
        pill: live ? WORKING_PILL : s.pill,
      }));
      void paintCanvas(threadId);
    },
    [paintCanvas],
  );

  // ------------------------------------------------------------
  // Chapter-level stream — the live chapter thread's node events.
  // ------------------------------------------------------------
  const liveChapterThread = state.canvas?.live ? state.canvas.threadId : null;
  const chapterStream = useMemo(
    () =>
      liveChapterThread
        ? {
            url: stageEventsUrl("synth", liveChapterThread),
            headers: { accept: "text/event-stream" },
            parse: (data: unknown): StageEvent => stageEventSchema.parse(data),
            isTerminal: isSynthTerminal,
          }
        : null,
    [liveChapterThread],
  );

  useEventStream(chapterStream, (event) => {
    setState((s) => ({ ...s, events: appendStageEvent(s.events, event, EVENT_LOG_LIMIT) }));
    if (isSynthTerminal(event)) {
      const status = String(event.status ?? "done");
      const wallMs = numberOr(event.wall_ms, 0) > 0 ? numberOr(event.wall_ms, 0) : null;
      setState((s) => {
        const chapterId = s.canvas?.chapterId ?? null;
        return {
          ...s,
          ...DETACHED,
          running: false,
          canceling: false,
          cells: chapterId
            ? s.cells.map((c) =>
                c.id === chapterId
                  ? { ...c, status: toChapterStatus(status), wallMs: wallMs ?? c.wallMs }
                  : c,
              )
            : s.cells,
          pill: status === "failed" ? terminalPill("failed") : IDLE_PILL,
        };
      });
      invalidatePipeline();
      return;
    }
    if (event.step) {
      const step = event.step;
      const text = synthLiveText(event);
      setState((s) => (text ? { ...s, liveLines: { ...s.liveLines, [step]: text } } : s));
      // A node's checkpoint commits a tick after its `done`: re-read the state so its stats reach the drawer.
      const threadId = liveChapterThread;
      if (event.kind === "done" && threadId) {
        const field = SYNTH_NODES.find((node) => node.id === step)?.field ?? null;
        void fetchGraphStateRetried("synth", threadId, field, () => attachedThread.current === threadId).then(
          (graphState) => {
            if (!graphState) return;
            setState((s) =>
              s.canvas?.threadId === threadId
                ? { ...s, values: graphState.values, next: graphState.next }
                : s,
            );
          },
        );
      }
    }
  });

  // ------------------------------------------------------------
  // Study-level stream — the orchestrator's events.
  // ------------------------------------------------------------
  const studyStream = useMemo(
    () =>
      state.studyThreadId
        ? {
            url: stageEventsUrl("synth", state.studyThreadId),
            headers: { accept: "text/event-stream" },
            parse: (data: unknown): StageEvent => stageEventSchema.parse(data),
          }
        : null,
    [state.studyThreadId],
  );

  useEventStream(studyStream, (event) => {
    if (event.step !== "study") return;
    const chapterId = String(event.chapter_id ?? "");
    switch (event.kind) {
      case "study_start": {
        const ids = stringList(event.chapter_ids);
        setState((s) => ({
          ...s,
          cells: cellsFromIds(ids),
          pill: { status: "working", label: `Working · 0/${ids.length}` },
          harmonize: IDLE_HARMONIZE,
        }));
        return;
      }
      case "chapter_running": {
        const threadId = String(event.chapter_thread_id ?? "");
        const position = numberOr(event.position, 0);
        const total = numberOr(event.n_total, 0);
        setState((s) => ({
          ...s,
          cells: s.cells.map((c) =>
            c.id === chapterId ? { ...c, status: "running", threadId: threadId || c.threadId } : c,
          ),
          pill: { status: "working", label: total > 0 ? `Working · ${position}/${total}` : "Working" },
        }));
        if (threadId) attachCanvas(threadId, chapterId, true);
        return;
      }
      case "chapter_done": {
        const status = String(event.status ?? "done");
        const wallMs = typeof event.wall_ms === "number" ? event.wall_ms : null;
        setState((s) => ({
          ...s,
          cells: s.cells.map((c) =>
            c.id === chapterId ? { ...c, status: toChapterStatus(status), wallMs: wallMs ?? c.wallMs } : c,
          ),
          ...(s.canvas?.chapterId === chapterId && s.canvas.live ? DETACHED : {}),
        }));
        if (status === "failed") overlays.toast(`Chapter failed: ${String(event.error || chapterId)}`);
        return;
      }
      case "chapter_ready":
        setCells((cells) => cells.map((c) => (c.id === chapterId ? { ...c, status: "done" } : c)));
        return;
      case "book_harmonize_start": {
        const chapters = numberOr(event.n_chapters, 0);
        setState((s) => ({
          ...s,
          harmonize: { status: "running", label: chapters > 0 ? `${chapters} chapters` : "" },
          pill: { status: "working", label: "Harmonizing chapters…" },
        }));
        return;
      }
      case "book_harmonize_skipped": {
        const label =
          event.reason === "fewer_than_2_rendered_chapters"
            ? "Skipped — needs 2 or more chapters"
            : "Skipped";
        setState((s) => ({ ...s, harmonize: { status: "skipped", label } }));
        return;
      }
      case "book_harmonize_done": {
        const label = harmonizeDoneLabel(
          numberOr(event.n_chapters_overwritten, 0),
          numberOr(event.n_chapters_with_issues, 0),
          event.cache_hit === true,
        );
        setState((s) => ({ ...s, harmonize: { status: "done", label } }));
        return;
      }
      case "study_done": {
        const completed = numberOr(event.n_completed, 0);
        const total = numberOr(event.n_total, 0);
        const finalStatus = String(event.final_status ?? "done");
        const wallMs = typeof event.total_wall_ms === "number" ? event.total_wall_ms : null;
        setState((s) => ({
          ...s,
          ...DETACHED,
          running: false,
          canceling: false,
          studyThreadId: null,
          elapsedMs: wallMs ?? s.elapsedMs,
          pill:
            finalStatus === "cancelled"
              ? { status: "cancelled", label: `Cancelled (${completed}/${total})` }
              : { status: "done", label: `Done (${completed}/${total})` },
        }));
        overlays.toast(
          finalStatus === "cancelled"
            ? `Study cancelled: ${completed}/${total} chapters done.`
            : finalStatus === "failed"
              ? `Study finished with failures: ${completed}/${total} chapters done.`
              : `All ${total} chapters synthesized.`,
        );
        invalidatePipeline();
        void hydratePlan();
        return;
      }
      default:
        return;
    }
  });

  // ------------------------------------------------------------
  // Recovery — a live run first (reconnect), else the chapter list.
  // ------------------------------------------------------------
  useEffect(() => {
    let alive = true;
    void (async () => {
      const active = await fetchSynthActive(slug);
      if (!alive) return;
      if (active?.active && active.study_thread_id) {
        setState((s) => ({
          ...s,
          running: true,
          studyThreadId: active.study_thread_id,
          studyStartedMs: active.started_ts ? active.started_ts * 1000 : null,
          pill: { status: "working", label: "Resuming study…" },
        }));
        invalidatePipeline();
        await hydratePlan();
        return;
      }
      if (active?.active && active.chapter_thread_id) {
        const cells = await hydratePlan();
        if (!alive) return;
        const chapterId = cells.find((c) => c.threadId === active.chapter_thread_id)?.id ?? null;
        attachCanvas(active.chapter_thread_id, chapterId, true);
        invalidatePipeline();
        return;
      }
      const cells = await hydratePlan();
      if (!alive) return;
      // With a plan, `hydratePlan` already set the pill (Done once every chapter is written, else Idle); with
      // none there is nothing to say, so drop the "Loading…" it started with.
      if (cells.length === 0) setState((s) => (s.running || s.starting ? s : { ...s, pill: IDLE_PILL }));
    })();
    return () => {
      alive = false;
    };
  }, [slug, hydratePlan, invalidatePipeline, attachCanvas]);

  // ------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------
  const start = useCallback(async () => {
    if (state.running || state.starting) return;
    setState((s) => ({ ...s, starting: true, error: null }));
    try {
      const outcome = await startSynthStudy(slug);
      if (outcome.kind === "locked") {
        overlays.notice(`Synth blocked — ${outcome.message ?? BLOCKED}`, "error");
        invalidatePipeline();
        return;
      }
      if (outcome.kind !== "study") {
        setState((s) => ({ ...s, pill: terminalPill("failed") }));
        return;
      }
      const startedMs = Date.now();
      setState((s) => {
        const cells = outcome.chapterIds.length > 0 ? cellsFromIds(outcome.chapterIds) : s.cells;
        return {
          ...s,
          running: true,
          studyThreadId: outcome.threadId,
          studyStartedMs: startedMs,
          cells,
          pill: { status: "working", label: `Working · 0/${cells.length}` },
          harmonize: IDLE_HARMONIZE,
        };
      });
      invalidatePipeline();
    } catch (error) {
      setState((s) => ({ ...s, pill: terminalPill("failed"), error: apiErrorMessage(error) }));
    } finally {
      setState((s) => ({ ...s, starting: false }));
    }
  }, [slug, state.running, state.starting, overlays, invalidatePipeline]);

  const cancel = useCallback(async () => {
    const threadId = state.studyThreadId ?? (state.canvas?.live ? state.canvas.threadId : null);
    if (!threadId || state.canceling) return;
    setState((s) => ({ ...s, canceling: true }));
    await cancelRun("synth", threadId);
    // The stream's terminal event is the intended release; if it never lands, free the UI anyway
    // (the server keeps draining its own cancel flag either way).
    setTimeout(() => {
      setState((s) =>
        s.canceling ? { ...s, ...DETACHED, running: false, canceling: false, studyThreadId: null } : s,
      );
      void hydratePlan();
    }, CANCEL_SAFETY_MS);
  }, [state.studyThreadId, state.canvas, state.canceling, hydratePlan]);

  const wipeChapter = useCallback(
    async (chapterId: string) => {
      const cell = state.cells.find((c) => c.id === chapterId);
      const confirmed = await overlays.confirm({
        title: "Wipe this chapter?",
        message: `"${cell?.title ?? chapterId}": deletes this chapter's synthesized output and cached stages. Other chapters and the planner cache are untouched. This cannot be undone.`,
        confirmLabel: "Wipe",
      });
      if (!confirmed) return;
      const outcome = await wipeSynthChapter(slug, chapterId);
      if (outcome !== "wiped") {
        overlays.toast(
          outcome === "busy" ? "A synth run is in flight — cancel it before wiping." : "Wipe failed.",
        );
        return;
      }
      setState((s) => ({
        ...s,
        cells: s.cells.map((c) =>
          c.id === chapterId ? { ...c, status: "pending", wallMs: null, threadId: null } : c,
        ),
        ...(s.canvas?.chapterId === chapterId && !s.canvas.live ? DETACHED : {}),
      }));
    },
    [slug, state.cells, overlays],
  );

  const runChapter = useCallback(
    async (chapterId: string) => {
      if (state.running || state.starting) return;
      setState((s) => ({ ...s, starting: true }));
      try {
        const active = await fetchSynthActive(slug);
        if (active?.active) {
          overlays.toast("A synth run is already in flight for this framework.");
          return;
        }
        const cell = state.cells.find((c) => c.id === chapterId);
        if (cell?.status === "done") {
          const confirmed = await overlays.confirm({
            title: "Re-synthesize this chapter?",
            message: `"${cell.title}" is already synthesized. Running it again deletes its current output and builds it from scratch.`,
            confirmLabel: "Re-synthesize",
          });
          if (!confirmed) return;
          if ((await wipeSynthChapter(slug, chapterId)) !== "wiped") {
            overlays.toast("Could not clear the old output; the chapter was not re-run.");
            return;
          }
          setCells((cells) =>
            cells.map((c) => (c.id === chapterId ? { ...c, status: "pending", wallMs: null } : c)),
          );
        }
        const outcome = await startSynthChapter(slug, chapterId);
        if (outcome.kind === "locked") {
          overlays.notice(`Synth blocked — ${outcome.message ?? BLOCKED}`, "error");
          invalidatePipeline();
          return;
        }
        if (outcome.kind !== "chapter") {
          overlays.toast("The server did not return a thread for this chapter.");
          return;
        }
        setCells((cells) =>
          cells.map((c) =>
            c.id === chapterId ? { ...c, status: "running", threadId: outcome.threadId } : c,
          ),
        );
        attachCanvas(outcome.threadId, chapterId, true);
      } catch (error) {
        overlays.toast(`Could not start the chapter — ${apiErrorMessage(error)}`);
      } finally {
        setState((s) => ({ ...s, starting: false }));
      }
    },
    [slug, state.running, state.starting, state.cells, overlays, invalidatePipeline, setCells, attachCanvas],
  );

  const selectChapter = useCallback(
    async (chapterId: string) => {
      if (state.running || state.canvas?.live) return;
      const cell = state.cells.find((c) => c.id === chapterId);
      if (!cell?.threadId || cell.status !== "done") return;
      const threadId = cell.threadId;
      setCells((cells) => cells.map((c) => ({ ...c, selected: c.id === chapterId })));
      attachCanvas(threadId, chapterId, false);
      setState((s) => ({ ...s, pill: { status: "done", label: `Reading · ${cell.title}` } }));
    },
    [state.running, state.canvas?.live, state.cells, setCells, attachCanvas],
  );

  const wipe = useCallback(async () => {
    const pipeline = await fetchPipelineState(slug);
    const confirmed = await overlays.confirm({
      title: "Wipe synth?",
      message: `Deletes this framework's synth cache (chapter artifacts and checkpoints). The planner cache is untouched.${deleteCascadeText(pipeline, "synth")} This cannot be undone.`,
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    if (!(await wipeSynth(slug))) {
      overlays.toast("Wipe failed. Try again.");
      return;
    }
    setState(SYNTH_EMPTY);
    invalidatePipeline();
    void hydratePlan();
  }, [slug, overlays, invalidatePipeline, hydratePlan]);

  // ------------------------------------------------------------
  // Derived view
  // ------------------------------------------------------------
  const studyRunning = state.running && state.studyThreadId !== null;
  const canvasRunning = Boolean(state.canvas?.live) && state.studyThreadId === null;
  const studyElapsed = useElapsed(studyRunning, state.studyStartedMs);
  const canvasElapsed = useElapsed(canvasRunning, state.canvasStartedMs);

  const views = useMemo(
    () => stageNodeViews(SYNTH_NODES, state.values, state.next, Boolean(state.canvas?.live), implemented),
    [state.values, state.next, state.canvas?.live, implemented],
  );

  return {
    running: state.running,
    starting: state.starting,
    canceling: state.canceling,
    pill: state.pill,
    elapsedMs: studyRunning ? studyElapsed : canvasRunning ? canvasElapsed : state.elapsedMs,
    cells: state.cells,
    counter: chapterCounter(state.cells),
    startLabel: synthStartLabel(state.cells),
    values: state.values,
    liveLines: state.liveLines,
    events: state.events,
    harmonize: state.harmonize,
    error: state.error,
    viewChapterId: state.canvas?.chapterId ?? null,
    canvasThreadId: state.canvas?.threadId ?? null,
    views,
    next: state.next,
    progress: stageProgressLabel(SYNTH_NODES, views, implemented),
    start,
    cancel,
    wipe,
    runChapter,
    wipeChapter,
    selectChapter,
  };
}
