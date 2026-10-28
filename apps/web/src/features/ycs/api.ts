/** YouTube Content Search — every Source-stage call, through the generated client. Responses are parsed
 * with their zod schema; command endpoints throw so the mutation layer can show `apiErrorMessage`. */

import {
  client,
  deleteYcsAgentsThreadsThreadId,
  deleteYcsAdminVideosVideoId,
  getYcsAdminIngestedChannels,
  getYcsAgentsHistoryThreadId,
  getYcsAgentsThreads,
  getYcsAgentsUsageThreadId,
  getYcsContentChannelVideos,
  getYcsContentPlaylistVideos,
  getYcsContentVideosPipelineExtractIdState,
  getYcsContentVideosPreview,
  postYcsAdminVideosBulkDelete,
  postYcsAgentsTurnsTurnIdCancel,
  postYcsContentChannelPipeline,
  postYcsContentPlaylistPipeline,
  postYcsContentSearch,
  postYcsContentVideosPipeline,
  postYcsContentVideosPipelineExtractIdRerun,
  postYcsContentVideosPipelineExtractIdStop,
  postYcsContentVideosPipelineExtractIdWipe,
} from "@/shared/api";

import type {
  AskUsage,
  ChannelInfo,
  Enumeration,
  HistoryRow,
  PipelinePhases,
  PipelineWipe,
  SearchRequestPayload,
  SearchResponse,
  ThreadSummary,
} from "./entities";
import {
  branchResponseSchema,
  channelsResponseSchema,
  enumerationSchema,
  historyResponseSchema,
  llmCountersSchema,
  pipelineSavedStateSchema,
  pipelineStartSchema,
  pipelineWipeSchema,
  searchResponseSchema,
  threadsResponseSchema,
} from "./entities";

/** yt-dlp metadata search. Throws on failure so the caller surfaces a readable line. */
export async function searchVideos(req: SearchRequestPayload): Promise<SearchResponse> {
  const result = await postYcsContentSearch({ body: req, throwOnError: true });
  return searchResponseSchema.parse(result.data);
}

/** One page of a channel's videos (~3–6 s per page on yt-dlp). */
export async function enumerateChannelVideos(
  id: string,
  offset: number,
  limit: number,
): Promise<Enumeration> {
  const result = await getYcsContentChannelVideos({ query: { id, offset, limit }, throwOnError: true });
  return enumerationSchema.parse(result.data);
}

/** One page of a playlist's videos. */
export async function enumeratePlaylistVideos(
  id: string,
  offset: number,
  limit: number,
): Promise<Enumeration> {
  const result = await getYcsContentPlaylistVideos({ query: { id, offset, limit }, throwOnError: true });
  return enumerationSchema.parse(result.data);
}

/** One page of metadata for a pasted batch of video ids. */
export async function previewVideos(
  ids: ReadonlyArray<string>,
  offset: number,
  limit: number,
): Promise<Enumeration> {
  const result = await getYcsContentVideosPreview({
    query: { ids: ids.join(","), offset, limit },
    throwOnError: true,
  });
  return enumerationSchema.parse(result.data);
}

/** The three phase ids a successful pipeline dispatch returns, for the ingestion redirect. */
export type PipelineQueued = { phases: PipelinePhases; videoIds: string[] };

/** Videos-tab dispatch: a picked subset (or the whole pasted batch) through the 3-phase pipeline. */
export async function queueVideosPipeline(body: {
  video_ids: string[];
  include_transcription: boolean;
  transcription_languages: string[] | null;
}): Promise<PipelineQueued | null> {
  const result = await postYcsContentVideosPipeline({ body, throwOnError: true });
  return parsePipelineStart(result.data);
}

/** Ingest-all dispatch for a channel: the server walks the entire channel in one yt-dlp call. */
export async function queueChannelPipeline(body: {
  channel_id: string;
  include_transcription: boolean;
  transcription_languages: string[] | null;
}): Promise<PipelineQueued | null> {
  const result = await postYcsContentChannelPipeline({ body, throwOnError: true });
  return parsePipelineStart(result.data);
}

/** Ingest-all dispatch for a playlist. */
export async function queuePlaylistPipeline(body: {
  playlist_id: string;
  include_transcription: boolean;
  transcription_languages: string[] | null;
}): Promise<PipelineQueued | null> {
  const result = await postYcsContentPlaylistPipeline({ body, throwOnError: true });
  return parsePipelineStart(result.data);
}

function parsePipelineStart(data: unknown): PipelineQueued | null {
  const body = pipelineStartSchema.parse(data);
  return body.phases ? { phases: body.phases, videoIds: body.video_ids } : null;
}

// ============================================================
// Ingestion stage — state, wipe/rerun, library actions
// ============================================================

/** The dispatch's video-id ordering, persisted server-side. */
export async function pipelineVideoIds(extractId: string): Promise<string[]> {
  const result = await getYcsContentVideosPipelineExtractIdState({
    path: { extract_id: extractId },
    throwOnError: true,
  });
  return pipelineSavedStateSchema.parse(result.data).video_ids;
}

/** Stop: the running phase gets SIGTERM, queued phases are cancelled; completed phases keep writes. */
export async function stopPipeline(extractId: string): Promise<void> {
  await postYcsContentVideosPipelineExtractIdStop({ path: { extract_id: extractId }, throwOnError: true });
}

/** Wipe: sweeps ES + Qdrant + Neo4j for the dispatch's videos (revokes in-flight phases first). */
export async function wipePipeline(extractId: string): Promise<PipelineWipe> {
  const result = await postYcsContentVideosPipelineExtractIdWipe({
    path: { extract_id: extractId },
    throwOnError: true,
  });
  return pipelineWipeSchema.parse(result.data);
}

/** Rerun the same dispatch from scratch; returns fresh phase ids for the ingestion redirect. */
export async function rerunPipeline(extractId: string): Promise<PipelineQueued | null> {
  const result = await postYcsContentVideosPipelineExtractIdRerun({
    path: { extract_id: extractId },
    throwOnError: true,
  });
  return parsePipelineStart(result.data);
}

/** Wipes ES + Qdrant + Neo4j for one video (entity nodes are left intact). */
export async function deleteLibraryVideo(videoId: string): Promise<void> {
  await deleteYcsAdminVideosVideoId({ path: { video_id: videoId }, throwOnError: true });
}

/** Bulk wipe for the selected rows. */
export async function bulkDeleteLibraryVideos(videoIds: readonly string[]): Promise<void> {
  await postYcsAdminVideosBulkDelete({ body: { video_ids: [...videoIds] }, throwOnError: true });
}

// ============================================================
// Ask stage — channels, threads, history, stream URL, usage
// ============================================================

/** Absolute URL for the ask SSE stream (the shared sse reader does its own fetch, bypassing the client). */
export function askStreamUrl(): string {
  return client.buildUrl({ url: "/api/v1/ycs/agents/search/stream" });
}

/** Channels available for the scope picker; de-ingested ids fall back to "Unknown channel" in the UI. */
export async function fetchChannels(): Promise<ChannelInfo[]> {
  const result = await getYcsAdminIngestedChannels({ throwOnError: true });
  return channelsResponseSchema.parse(result.data).items.filter((item) => item.channel_id);
}

/** Saved threads, most recent first. */
export async function fetchThreadList(): Promise<ThreadSummary[]> {
  const result = await getYcsAgentsThreads({ throwOnError: true });
  return threadsResponseSchema.parse(result.data).items.filter((item) => item.thread_id);
}

/** Persisted turns of a thread, oldest first. */
export async function fetchHistory(threadId: string): Promise<HistoryRow[]> {
  const result = await getYcsAgentsHistoryThreadId({
    path: { thread_id: threadId },
    throwOnError: true,
  });
  return historyResponseSchema.parse(result.data).items;
}

/** Per-thread token totals — counts only; the per-model breakdown is deliberately left unparsed. */
export async function fetchAskUsage(threadId: string): Promise<AskUsage | null> {
  const result = await getYcsAgentsUsageThreadId({ path: { thread_id: threadId }, throwOnError: true });
  return llmCountersSchema.parse(result.data);
}

/** Cancel (or, for a finished turn, delete) one turn's row — Stop and Regenerate share it. Best-effort:
 * the callers proceed even when the row is already gone. */
export async function cancelAskTurn(turnId: number): Promise<boolean> {
  try {
    await postYcsAgentsTurnsTurnIdCancel({ path: { turn_id: turnId }, throwOnError: true });
    return true;
  } catch {
    return false;
  }
}

/** Fork the thread at (and including) `upToCreatedAt`; whole thread when null. */
export async function branchThread(
  threadId: string,
  upToCreatedAt: string | null,
): Promise<{ newThreadId: string; copied: number } | null> {
  // The branch endpoint reads the raw request body, so the generated spec types it as `never` — call
  // the generated client's method directly to pass `up_to_created_at`.
  const result = await client.post({
    url: "/api/v1/ycs/agents/threads/{thread_id}/branch",
    path: { thread_id: threadId },
    body: { up_to_created_at: upToCreatedAt },
    throwOnError: true,
  });
  const data = branchResponseSchema.parse(result.data);
  return data.new_thread_id ? { newThreadId: data.new_thread_id, copied: data.copied } : null;
}

/** Deletes a thread; returns false when the caller should show a failure notice. */
export async function deleteThread(threadId: string): Promise<boolean> {
  try {
    await deleteYcsAgentsThreadsThreadId({ path: { thread_id: threadId }, throwOnError: true });
    return true;
  } catch {
    return false;
  }
}
