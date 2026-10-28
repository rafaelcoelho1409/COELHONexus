/** YouTube Content Search — zod schemas over the generated response types. The generated response shapes
 * for these endpoints are open objects, so every response is parsed here before the UI reads it. */

import { z } from "zod";

/** One row of a search result page or of a channel/playlist enumeration. Mirrors yt-dlp's
 * `--flat-playlist --dump-single-json` projection; `kind` may be "channel" or "playlist" too. */
export const videoSnippetSchema = z.looseObject({
  id: z.string().catch(""),
  kind: z.string().catch("video"),
  title: z.string().nullish().catch(null),
  url: z.string().catch(""),
  duration: z.number().nullish().catch(null),
  duration_string: z.string().nullish().catch(null),
  view_count: z.number().nullish().catch(null),
  like_count: z.number().nullish().catch(null),
  channel: z.string().nullish().catch(null),
  channel_id: z.string().nullish().catch(null),
  channel_url: z.string().nullish().catch(null),
  thumbnail: z.string().nullish().catch(null),
  description: z.string().nullish().catch(null),
  upload_date: z.string().nullish().catch(null),
  timestamp: z.number().nullish().catch(null),
  release_timestamp: z.number().nullish().catch(null),
  live_status: z.string().nullish().catch(null),
  availability: z.string().nullish().catch(null),
  video_count: z.number().nullish().catch(null),
});
export type VideoSnippet = z.infer<typeof videoSnippetSchema>;

/** Envelope of `POST /content/search`. */
export const searchResponseSchema = z.looseObject({
  query: z.string().catch(""),
  total: z.number().catch(0),
  results: z.array(videoSnippetSchema).catch([]),
  fetched_for: z.number().catch(0),
  elapsed_s: z.number().catch(0),
});
export type SearchResponse = z.infer<typeof searchResponseSchema>;

/** Paginated video listing inside one channel, playlist or pasted-ids batch. */
export const enumerationSchema = z.looseObject({
  source: z.string().catch(""),
  source_id: z.string().catch(""),
  title: z.string().nullish().catch(null),
  channel: z.string().nullish().catch(null),
  total: z.number().nullish().catch(null),
  offset: z.number().catch(0),
  limit: z.number().catch(0),
  has_more: z.boolean().catch(false),
  items: z.array(videoSnippetSchema).catch([]),
});
export type Enumeration = z.infer<typeof enumerationSchema>;

/** The three Celery phase ids a pipeline dispatch returns. Missing phases mean the dispatch failed. */
const pipelinePhasesSchema = z.looseObject({
  extract: z.string(),
  qdrant: z.string(),
  neo4j: z.string(),
});
export type PipelinePhases = z.infer<typeof pipelinePhasesSchema>;

export const pipelineStartSchema = z.looseObject({
  phases: pipelinePhasesSchema.nullish().catch(null),
  video_ids: z.array(z.string()).catch([]),
});

/** What the Search tab posts. The unions mirror the generated `SearchRequest` so the parsed value is
 * directly assignable; `z.object` also drops anything the backend's `extra="forbid"` model would 422. */
/** Running counters of the shared extract task (`meta` slot). The sub-phase string walks
 * metadata → metadata_done → transcription → es_indexing; `all_items` arrives once yt-dlp metadata is
 * done, seeding the per-video table with titles. */
const taskMetaSchema = z.looseObject({
  phase: z.string().nullish().catch(null),
  current: z.number().nullish().catch(null),
  total: z.number().nullish().catch(null),
  piece_current: z.number().nullish().catch(null),
  piece_total: z.number().nullish().catch(null),
  failed_ids: z.array(z.string()).nullish().catch(null),
  completed_ids: z.array(z.string()).nullish().catch(null),
  no_transcript_ids: z.array(z.string()).nullish().catch(null),
  current_item: z
    .looseObject({ id: z.string().catch(""), title: z.string().nullish().catch(null) })
    .nullish()
    .catch(null),
  all_items: z
    .array(
      z.looseObject({
        id: z.string().catch(""),
        title: z.string().nullish().catch(null),
        channel: z.string().nullish().catch(null),
      }),
    )
    .nullish()
    .catch(null),
});
export type TaskMeta = z.infer<typeof taskMetaSchema>;

/** SUCCESS result dict. One loose schema covers all four stores' payloads — every field is optional and
 * defaults to a falsy value, so a Playwright result parses fine even though it lacks Qdrant's keys. */
const taskResultSchema = z.looseObject({
  total_videos: z.number().catch(0),
  metadata: z
    .looseObject({ indexed: z.number().catch(0) })
    .nullish()
    .catch(null),
  transcriptions: z
    .looseObject({
      indexed: z.number().catch(0),
      cached: z.number().catch(0),
      fetch_failed: z.number().catch(0),
      failed: z.number().catch(0),
      no_transcript: z.number().catch(0),
    })
    .nullish()
    .catch(null),
  no_transcript_ids: z.array(z.string()).catch([]),
  failed_ids: z.array(z.string()).catch([]),
  completed_ids: z.array(z.string()).catch([]),
  total_transcripts: z.number().catch(0),
  total_chunks: z.number().catch(0),
  points_upserted: z.number().catch(0),
  videos_unchanged: z.number().catch(0),
  nodes_created: z.number().catch(0),
  relationships_created: z.number().catch(0),
  entities_merged: z.number().catch(0),
});
export type TaskResult = z.infer<typeof taskResultSchema>;

/** Snapshot of one poll of `GET /admin/task/{task_id}` — the Celery-task shape the Source dispatches
 * feed. `meta` carries the running counters; on SUCCESS `result` carries the outcome dict (the meta slot
 * then holds it too, mirroring the legacy console). */
export const taskSnapshotSchema = z.looseObject({
  task_id: z.string().catch(""),
  state: z.string().catch("PENDING"),
  meta: taskMetaSchema.nullish().catch(null),
  result: taskResultSchema.nullish().catch(null),
  error: z.string().nullish().catch(null),
});

/** What `GET /content/videos/pipeline/{extract_id}/state` returns — the dispatch's video-id ordering. */
export const pipelineSavedStateSchema = z.looseObject({
  video_ids: z.array(z.string()).catch([]),
});

/** Response of `POST …/pipeline/{extract_id}/wipe`. */
export const pipelineWipeSchema = z.looseObject({
  status: z.string().catch(""),
  summary: z
    .looseObject({
      es: z
        .looseObject({ metadata_deleted: z.number().catch(0), transcripts_deleted: z.number().catch(0) })
        .catch(() => ({ metadata_deleted: 0, transcripts_deleted: 0 })),
      qdrant: z.looseObject({ qdrant_deleted: z.number().catch(0) }).catch(() => ({ qdrant_deleted: 0 })),
      neo4j: z
        .looseObject({
          documents_deleted: z.number().catch(0),
          videos_deleted: z.number().catch(0),
          entities_swept: z.number().catch(0),
        })
        .catch(() => ({ documents_deleted: 0, videos_deleted: 0, entities_swept: 0 })),
    })
    .nullish()
    .catch(null),
});
export type PipelineWipe = z.infer<typeof pipelineWipeSchema>;

/** One row of `GET /admin/videos` — the flat list of every ingested video. */
export const libraryItemSchema = z.looseObject({
  video_id: z.string().catch(""),
  title: z.string().nullish().catch(null),
  channel: z.string().nullish().catch(null),
  status: z.string().catch("unknown"),
  view_count: z.number().nullish().catch(null),
  like_count: z.number().nullish().catch(null),
  duration: z.number().nullish().catch(null),
  duration_string: z.string().nullish().catch(null),
  upload_date: z.string().nullish().catch(null),
  thumbnail: z.string().nullish().catch(null),
  webpage_url: z.string().nullish().catch(null),
  transcript_length: z.number().nullish().catch(null),
  transcript_langs: z.array(z.string()).nullish().catch(null),
  entity_count: z.number().nullish().catch(null),
});
export type LibraryItem = z.infer<typeof libraryItemSchema>;

export const libraryResponseSchema = z.looseObject({
  items: z.array(libraryItemSchema).catch([]),
  total: z.number().catch(0),
  returned: z.number().catch(0),
  offset: z.number().catch(0),
  limit: z.number().catch(0),
});

/** Facet counts. The backend's keys are plural (`statuses`) while the query params are singular — the
 * lookup maps them explicitly. */
const facetEntrySchema = z.looseObject({
  key: z.string().nullish().catch(null),
  channel_id: z.string().nullish().catch(null),
  label: z.string().nullish().catch(null),
  channel: z.string().nullish().catch(null),
  count: z.number().nullish().catch(null),
  video_count: z.number().nullish().catch(null),
});
export type FacetEntry = z.infer<typeof facetEntrySchema>;

export const libraryFacetsSchema = z.looseObject({
  channels: z.array(facetEntrySchema).catch([]),
  languages: z.array(facetEntrySchema).catch([]),
  statuses: z.array(facetEntrySchema).catch([]),
});
export type LibraryFacets = z.infer<typeof libraryFacetsSchema>;

/** `GET /admin/pipeline/{extract_id}/llm-counters` — token counters only; per-model breakdown is
 * deliberately left unparsed. */
export const llmCountersSchema = z.looseObject({
  total: z
    .looseObject({
      calls: z.number().catch(0),
      tokens_in: z.number().catch(0),
      tokens_out: z.number().catch(0),
      reasoning_tokens: z.number().catch(0),
    })
    .nullish()
    .catch(null),
});

/** The thread-total usage payload shape (counts only). */
export type AskUsage = z.infer<typeof llmCountersSchema>;

export const searchRequestSchema = z.object({
  query: z.string(),
  max_results: z.number().optional(),
  sort_by_date: z.boolean().optional(),
  duration: z.enum(["Under 4 minutes", "4 - 20 minutes", "Over 20 minutes"]).nullish(),
  duration_min: z.number().nullish(),
  duration_max: z.number().nullish(),
  date_after: z.string().nullish(),
  date_before: z.string().nullish(),
  min_views: z.number().nullish(),
  max_views: z.number().nullish(),
  min_likes: z.number().nullish(),
  is_live: z.boolean().nullish(),
  live_status: z.enum(["not_live", "is_live", "is_upcoming", "was_live", "post_live"]).nullish(),
  availability: z
    .enum(["public", "unlisted", "private", "premium_only", "subscriber_only", "needs_auth"])
    .nullish(),
  age_limit: z.number().nullish(),
  title_contains: z.string().nullish(),
  description_contains: z.string().nullish(),
  channel_name: z.string().nullish(),
  exclude_shorts: z.boolean().optional(),
  kind_filter: z.enum(["video", "channel", "playlist"]).nullish(),
});
export type SearchRequestPayload = z.infer<typeof searchRequestSchema>;

// ============================================================
// Ask stage — channel scope, threads, history rows, SSE events
// ============================================================

/** `GET /admin/ingested-channels` — one row per indexed channel. */
export const channelInfoSchema = z.looseObject({
  channel_id: z.string().catch(""),
  channel: z.string().catch("Unknown channel"),
  video_count: z.number().int().catch(0),
});
export type ChannelInfo = z.infer<typeof channelInfoSchema>;

export const channelsResponseSchema = z.looseObject({
  items: z.array(channelInfoSchema).catch([]),
});

/** `GET /agents/threads` — one row per saved conversation. */
export const threadSummarySchema = z.looseObject({
  thread_id: z.string().catch(""),
  first_question: z.string().catch(""),
  turn_count: z.number().int().catch(0),
  last_seen: z.string().catch(""),
});
export type ThreadSummary = z.infer<typeof threadSummarySchema>;

export const threadsResponseSchema = z.looseObject({
  items: z.array(threadSummarySchema).catch([]),
});

/** One retrieved source: shown in the sources rail and cited inline as `[N]`. */
export const citationSchema = z.looseObject({
  title: z.string().catch(""),
  url: z.string().catch(""),
  video_id: z.string().catch(""),
  channel: z.string().catch(""),
  snippet: z.string().catch(""),
  source: z.string().catch(""),
});
export type AskCitation = z.infer<typeof citationSchema>;

/** One sub-question card of a Deep run (persisted card shape). */
export const askSubQuestionSchema = z.looseObject({
  question: z.string().catch(""),
  status: z.string().catch("queued"),
  answer: z.string().nullish().catch(null),
  answer_preview: z.string().nullish().catch(null),
});
export type AskSubQuestion = z.infer<typeof askSubQuestionSchema>;

/** A `sub_results` event item — different keys than the persisted card shape. */
const subResultSchema = z.looseObject({
  sub_question: z.string().catch(""),
  answer: z.string().catch(""),
});

/** The persisted per-turn trace (Postgres `conversation_history.thinking_state` JSONB). */
export const thinkingStateSchema = z.looseObject({
  stages: z
    .record(
      z.string(),
      z.looseObject({ status: z.string().catch(""), action: z.string().catch("") }),
    )
    .nullish()
    .catch(null),
  deep: z
    .looseObject({
      sub_questions: z.array(askSubQuestionSchema).catch([]),
      research_plan: z.string().catch(""),
      confidence_score: z.number().nullish().catch(null),
    })
    .nullish()
    .catch(null),
  channel_ids: z.array(z.string()).nullish().catch(null),
  citations: z.array(citationSchema).nullish().catch(null),
  duration_ms: z.number().nullish().catch(null),
  usage: llmCountersSchema.nullish().catch(null),
  _seq: z.number().nullish().catch(null),
});
export type AskThinkingState = z.infer<typeof thinkingStateSchema>;

/** One persisted turn row of `GET /agents/history/{thread_id}`. */
export const historyRowSchema = z.looseObject({
  id: z.number().nullish().catch(null),
  question: z.string().catch(""),
  answer: z.string().catch(""),
  mode: z.string().catch(""),
  created_at: z.string().catch(""),
  thinking_state: thinkingStateSchema.nullish().catch(null),
});
export type HistoryRow = z.infer<typeof historyRowSchema>;

export const historyResponseSchema = z.looseObject({
  items: z.array(historyRowSchema).catch([]),
});

/** One LangGraph node update of the `POST /agents/search/stream` SSE. */
export const askEventSchema = z.looseObject({
  node: z.string().catch("unknown"),
  turn_id: z.number().nullish().catch(null),
  mode: z.string().nullish().catch(null),
  documents: z.unknown().nullish().catch(null),
  document_count: z.number().nullish().catch(null),
  generation: z.string().nullish().catch(null),
  confidence_score: z.number().nullish().catch(null),
  citations: z.array(citationSchema).nullish().catch(null),
  sub_questions: z.array(z.string()).nullish().catch(null),
  research_plan: z.string().nullish().catch(null),
  sub_results: z.array(subResultSchema).nullish().catch(null),
  latest_sub_question: z.string().nullish().catch(null),
  latest_sub_answer: z.string().nullish().catch(null),
  latest_sub_answer_preview: z.string().nullish().catch(null),
  duration_ms: z.number().nullish().catch(null),
  usage: llmCountersSchema.nullish().catch(null),
  error: z.string().nullish().catch(null),
});
export type AskEvent = z.infer<typeof askEventSchema>;

/** `POST /agents/threads/{thread_id}/branch` — the forked thread's id + copied turn count. */
export const branchResponseSchema = z.looseObject({
  new_thread_id: z.string().catch(""),
  copied: z.number().int().catch(0),
});
