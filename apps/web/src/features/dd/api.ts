/** Docs Distiller — every planner/synth/pipeline call, through the generated client. Each response is
 * parsed with its zod schema; reads that may fail quietly resolve to an empty value instead of throwing. */

import {
  client,
  deleteDdPlannerSlugWipe,
  deleteDdSynthSlugWipe,
  getDdPipelineActive,
  getDdPipelineSlugState,
  getDdPlannerDebugGraphThreadIdLlmCounters,
  getDdPlannerDebugGraphThreadIdState,
  getDdPlannerInfo,
  getDdPlannerRecent,
  getDdPlannerSlugActive,
  getDdPlannerSlugHealth,
  getDdPlannerSlugTiming,
  getDdSynthDebugGraphThreadIdLlmCounters,
  getDdSynthDebugGraphThreadIdState,
  getDdSynthInfo,
  getDdSynthRecent,
  getDdSynthSlugActive,
  getDdSynthSlugStudyChapterIdArtifactArtifactName,
  getDdSynthSlugStudyChapters,
  postDdPlannerSlug,
  postDdPlannerThreadIdCancel,
  postDdPlannerThreadIdResume,
  postDdSynthSlug,
  postDdSynthThreadIdCancel,
} from "@/shared/api";

import type {
  GraphState,
  PipelineActive,
  PipelineState,
  PlannerHealth,
  StageActiveRun,
  StageInfo,
  SynthActiveRun,
  SynthChapters,
  UsagePayload,
} from "./entities";
import {
  graphStateSchema,
  pipelineActiveSchema,
  pipelineStateSchema,
  plannerHealthSchema,
  stageActiveRunSchema,
  stageInfoSchema,
  stageRecentSchema,
  stageStartResponseSchema,
  stageTimingSchema,
  synthActiveRunSchema,
  synthChaptersSchema,
  synthStartResponseSchema,
  usagePayloadSchema,
} from "./entities";
import {
  CHAPTER_ARTIFACT,
  GRAPH_STATE_RETRY_DELAYS_MS,
  PLANNER_MODE,
  type RunStage,
  SYNTH_MODE,
} from "./params";

async function orFallback<T>(read: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await read();
  } catch {
    return fallback;
  }
}

// ============================================================
// Reads
// ============================================================

/** Which graph nodes the server implements. Throws, so the query layer can show its own error state. */
export async function fetchStageInfo(stage: RunStage): Promise<StageInfo> {
  const result =
    stage === "planner"
      ? await getDdPlannerInfo({ throwOnError: true })
      : await getDdSynthInfo({ throwOnError: true });
  return stageInfoSchema.parse(result.data);
}

/** Latest checkpoint thread per slug. */
export function fetchStageRecent(stage: RunStage): Promise<Array<{ slug: string; thread_id: string }>> {
  return orFallback(async () => {
    const result =
      stage === "planner"
        ? await getDdPlannerRecent({ throwOnError: true })
        : await getDdSynthRecent({ throwOnError: true });
    return stageRecentSchema.parse(result.data).recent;
  }, []);
}

export function fetchPlannerActive(slug: string): Promise<StageActiveRun | null> {
  return orFallback(async () => {
    const result = await getDdPlannerSlugActive({ path: { slug }, throwOnError: true });
    return stageActiveRunSchema.parse(result.data);
  }, null);
}

export function fetchSynthActive(slug: string): Promise<SynthActiveRun | null> {
  return orFallback(async () => {
    const result = await getDdSynthSlugActive({ path: { slug }, throwOnError: true });
    return synthActiveRunSchema.parse(result.data);
  }, null);
}

export function fetchGraphState(stage: RunStage, threadId: string): Promise<GraphState | null> {
  return orFallback(async () => {
    const path = { thread_id: threadId };
    const result =
      stage === "planner"
        ? await getDdPlannerDebugGraphThreadIdState({ path, throwOnError: true })
        : await getDdSynthDebugGraphThreadIdState({ path, throwOnError: true });
    return graphStateSchema.parse(result.data);
  }, null);
}

/** Re-reads the checkpoint until `expectedField` lands (it commits a tick after the stream's `done`). */
export async function fetchGraphStateRetried(
  stage: RunStage,
  threadId: string,
  expectedField: string | null,
  stillWanted: () => boolean,
): Promise<GraphState | null> {
  for (const [attempt, delay] of GRAPH_STATE_RETRY_DELAYS_MS.entries()) {
    if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, delay));
    if (!stillWanted()) return null;
    const graphState = await fetchGraphState(stage, threadId);
    if (graphState && (!expectedField || expectedField in graphState.values)) return graphState;
  }
  return null;
}

/** Persisted wall-clock of the last planner run, in ms (0 when unknown). */
export function fetchPlannerTotalMs(slug: string): Promise<number> {
  return orFallback(async () => {
    const result = await getDdPlannerSlugTiming({ path: { slug }, throwOnError: true });
    return stageTimingSchema.parse(result.data).total_wall_ms;
  }, 0);
}

/** LLM calls and tokens of one run thread; null when the counters cannot be read. */
export function fetchUsage(stage: RunStage, threadId: string): Promise<UsagePayload | null> {
  return orFallback(async () => {
    const path = { thread_id: threadId };
    const result =
      stage === "planner"
        ? await getDdPlannerDebugGraphThreadIdLlmCounters({ path, throwOnError: true })
        : await getDdSynthDebugGraphThreadIdLlmCounters({ path, throwOnError: true });
    return usagePayloadSchema.parse(result.data);
  }, null);
}

/** How the latest plan of a framework was produced; null when unknown. */
export function fetchPlannerHealth(slug: string): Promise<PlannerHealth | null> {
  return orFallback(async () => {
    const result = await getDdPlannerSlugHealth({ path: { slug }, throwOnError: true });
    return plannerHealthSchema.parse(result.data);
  }, null);
}

export function fetchPipelineState(slug: string): Promise<PipelineState | null> {
  return orFallback(async () => {
    const result = await getDdPipelineSlugState({ path: { slug }, throwOnError: true });
    return pipelineStateSchema.parse(result.data);
  }, null);
}

export function fetchPipelineActive(): Promise<PipelineActive | null> {
  return orFallback(async () => {
    const result = await getDdPipelineActive({ throwOnError: true });
    return pipelineActiveSchema.parse(result.data);
  }, null);
}

export function fetchSynthChapters(slug: string): Promise<SynthChapters | null> {
  return orFallback(async () => {
    const result = await getDdSynthSlugStudyChapters({ path: { slug }, throwOnError: true });
    return synthChaptersSchema.parse(result.data);
  }, null);
}

/** Markdown of a rendered chapter. Throws when the artifact is missing so the reader can say so. */
export async function fetchChapterMarkdown(slug: string, chapterId: string): Promise<string> {
  const result = await getDdSynthSlugStudyChapterIdArtifactArtifactName({
    path: { slug, chapter_id: chapterId, artifact_name: CHAPTER_ARTIFACT },
    throwOnError: true,
  });
  return typeof result.data === "string" ? result.data : "";
}

/** SSE endpoint of a run. Streams are not typed in the OpenAPI spec, so `shared/sse` reads this URL;
 * building it from the route template keeps the client's base URL and path encoding. */
export function stageEventsUrl(stage: RunStage, threadId: string): string {
  return client.buildUrl({
    url:
      stage === "planner" ? "/api/v1/dd/planner/{thread_id}/events" : "/api/v1/dd/synth/{thread_id}/events",
    path: { thread_id: threadId },
  });
}

// ============================================================
// Starting runs
// ============================================================

/** `locked` = another run holds the pipeline lock; the message says which. */
export type StartOutcome = { kind: "started" } | { kind: "locked"; message: string | null };

/** An existing thread resumes (only uncached nodes run); a fresh slug starts a new thread. */
export async function startPlanner(slug: string, threadId: string, resume: boolean): Promise<StartOutcome> {
  const result = resume
    ? await postDdPlannerThreadIdResume({ path: { thread_id: threadId }, throwOnError: true })
    : await postDdPlannerSlug({
        path: { slug },
        query: { mode: PLANNER_MODE, thread_id: threadId },
        throwOnError: true,
      });
  const body = stageStartResponseSchema.parse(result.data);
  return body.status === "locked" ? { kind: "locked", message: body.message } : { kind: "started" };
}

export type SynthStartOutcome =
  | { kind: "locked"; message: string | null }
  | { kind: "study"; threadId: string; chapterIds: string[] }
  | { kind: "chapter"; threadId: string }
  | { kind: "rejected" }; // accepted, but the server named no thread to follow

/** Study mode: the orchestrator runs every chapter and skips the rendered ones, so a partial book resumes. */
export async function startSynthStudy(slug: string): Promise<SynthStartOutcome> {
  const result = await postDdSynthSlug({ path: { slug }, query: { mode: SYNTH_MODE }, throwOnError: true });
  const common = stageStartResponseSchema.parse(result.data);
  if (common.status === "locked") return { kind: "locked", message: common.message };
  const body = synthStartResponseSchema.parse(result.data);
  return body.study_thread_id
    ? { kind: "study", threadId: body.study_thread_id, chapterIds: body.chapter_ids ?? [] }
    : { kind: "rejected" };
}

/** Single-chapter escape hatch. */
export async function startSynthChapter(slug: string, chapterId: string): Promise<SynthStartOutcome> {
  const result = await postDdSynthSlug({
    path: { slug },
    query: { chapter_id: chapterId, mode: SYNTH_MODE },
    throwOnError: true,
  });
  const common = stageStartResponseSchema.parse(result.data);
  if (common.status === "locked") return { kind: "locked", message: common.message };
  const body = synthStartResponseSchema.parse(result.data);
  return body.thread_id ? { kind: "chapter", threadId: body.thread_id } : { kind: "rejected" };
}

// ============================================================
// Stopping and wiping
// ============================================================

/** Best effort: the stream's terminal event (or the safety timer) is what releases the UI. */
export async function cancelRun(stage: RunStage, threadId: string): Promise<void> {
  try {
    const path = { thread_id: threadId };
    if (stage === "planner") await postDdPlannerThreadIdCancel({ path });
    else await postDdSynthThreadIdCancel({ path });
  } catch {
    // the server keeps draining either way
  }
}

/** True when the cache was wiped. */
export async function wipePlanner(slug: string): Promise<boolean> {
  return orFallback(async () => {
    const { response } = await deleteDdPlannerSlugWipe({ path: { slug } });
    return response?.ok ?? false;
  }, false);
}

/** True when the cache was wiped. */
export async function wipeSynth(slug: string): Promise<boolean> {
  return orFallback(async () => {
    const { response } = await deleteDdSynthSlugWipe({ path: { slug } });
    return response?.ok ?? false;
  }, false);
}

/** `busy` = a synth run is in flight (HTTP 409) and has to be cancelled first. */
export async function wipeSynthChapter(
  slug: string,
  chapterId: string,
): Promise<"wiped" | "busy" | "failed"> {
  return orFallback(async () => {
    const { response } = await deleteDdSynthSlugWipe({ path: { slug }, query: { chapter_id: chapterId } });
    if (response?.ok) return "wiped" as const;
    return response?.status === 409 ? ("busy" as const) : ("failed" as const);
  }, "failed" as const);
}
