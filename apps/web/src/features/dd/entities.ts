/** Docs Distiller — zod schemas for every response the DD UI reads. The
 * generated client types these endpoints as open objects, so every payload
 * is parsed here before use (AGENTS.md contract). */

import { z } from "zod";

/** GET /api/v1/dd/resolver — catalog tiles. */
const catalogEntrySchema = z.looseObject({
  slug: z.string(),
  name: z.string(),
  category: z.string().nullable().catch(null),
  tier: z.number().nullable().catch(null),
  logo: z.string().nullable().catch(null),
  logos: z.array(z.string()).nullable().catch(null),
});
export type CatalogEntry = z.infer<typeof catalogEntrySchema>;

/** GET /api/v1/dd/resolver — the whole catalog (array). */
export const resolverCatalogSchema = z.array(catalogEntrySchema);

/** GET /api/v1/dd/ingestion — ingested library list. */
const libraryItemSchema = z.looseObject({
  slug: z.string(),
  framework_name: z.string().nullable().catch(null),
  framework_slug: z.string().nullable().catch(null),
  page_count: z.number().nullable().catch(null),
  ingested_at: z.number().nullable().catch(null),
  tier_kind: z.string().nullable().catch(null),
  logo: z.string().nullable().catch(null),
  logos: z.array(z.string()).nullable().catch(null),
});
export type LibraryItem = z.infer<typeof libraryItemSchema>;
export const libraryListSchema = z.array(libraryItemSchema);

/** Manifest entry — one ingested page. `idx` is the array position used by
 * GET /api/v1/dd/ingestion/{slug}/pages/{idx}. */
const manifestEntrySchema = z.looseObject({
  idx: z.number(),
  /** Storage key of the page; manifests written before it was stored have none. */
  key: z.string().nullable().catch(null),
  title: z.string().nullable().catch(null),
  slug: z.string().nullable().catch(null),
  tier: z.string().nullable().catch(null),
  bytes: z.number().nullable().catch(null),
  url: z.string().nullable().catch(null),
  source_path: z.string().nullable().catch(null),
  section: z.string().nullable().catch(null),
  notes: z.string().nullable().catch(null),
});
export type ManifestEntry = z.infer<typeof manifestEntrySchema>;

/** GET /api/v1/dd/ingestion/{slug}/manifest. */
export const manifestSchema = z.looseObject({
  framework_name: z.string().nullable().catch(null),
  framework_slug: z.string().nullable().catch(null),
  entries: z.array(manifestEntrySchema),
  total_bytes: z.number().nullable().catch(null),
  tier_kind: z.string().nullable().catch(null),
  ingested_at: z.number().nullable().catch(null),
});
export type Manifest = z.infer<typeof manifestSchema>;

/** GET /api/v1/dd/ingestion/{slug}/pages/{idx} — page body. */
export const pageBodySchema = z.looseObject({ body: z.string() });

/** GET /api/v1/dd/runs/{run_id} — live progress + manifest snapshot.
 * `post` is record_post's summary (split/dedup result); either field may
 * be absent on early/tier-only payloads. */
export const runStatusSchema = z.looseObject({
  progress: z
    .looseObject({
      tier: z.string().nullable().catch(null),
      status: z.string().nullable().catch(null),
      current: z.number().nullable().catch(null),
      total: z.number().nullable().catch(null),
      last_url: z.string().nullable().catch(null),
      error: z.string().nullable().catch(null),
      updated_at: z.number().nullable().catch(null),
    })
    .nullish()
    .catch(null),
  post: z
    .looseObject({
      was_split: z.boolean().nullable().catch(null),
      input_files: z.number().nullable().catch(null),
      output_files: z.number().nullable().catch(null),
      duplicates_dropped: z.number().nullable().catch(null),
      stubs_dropped: z.number().nullable().catch(null),
    })
    .nullish()
    .catch(null),
});
export type RunStatus = z.infer<typeof runStatusSchema>;

/** GET /api/v1/dd/runs/active — in-flight runs for page-reload recovery. */
export const activeRunsSchema = z.looseObject({
  active: z.array(
    z.looseObject({
      run_id: z.string(),
      slug: z.string().nullable().catch(null),
      progress: runStatusSchema.shape.progress,
    }),
  ),
});

/** POST /api/v1/dd/runs — cached | queued | locked. */
export const startRunResponseSchema = z.looseObject({
  status: z.enum(["cached", "queued", "locked"]),
  run_id: z.string().optional(),
  message: z.string().optional(),
});

// ============================================================
// Pipeline — cross-stage state + active runs
// ============================================================

/** GET /api/v1/dd/pipeline/{slug}/state — per-stage completion booleans
 * used for wipe cascade warnings and start gating. */
export const pipelineStateSchema = z.looseObject({
  ingestion: z.boolean().catch(false),
  planner: z.boolean().catch(false),
  synth: z.boolean().catch(false),
  study: z.boolean().catch(false),
});
export type PipelineState = z.infer<typeof pipelineStateSchema>;

/** GET /api/v1/dd/pipeline/active — the live planner/synth run anywhere
 * (cross-stage collision gate: planner and synth never run together). */
export const pipelineActiveSchema = z.looseObject({
  planner: z
    .looseObject({
      slug: z.string(),
      thread_id: z.string(),
    })
    .nullable()
    .catch(null),
  synth: z
    .looseObject({
      slug: z.string(),
      thread_id: z.string(),
    })
    .nullable()
    .catch(null),
});
export type PipelineActive = z.infer<typeof pipelineActiveSchema>;

// ============================================================
// Stage graphs — planner + synth run state (shared shape)
// ============================================================

/** GET /api/v1/dd/{stage}/info — which graph nodes the server implements. */
export const stageInfoSchema = z.looseObject({
  implemented: z.array(z.string()),
});
export type StageInfo = z.infer<typeof stageInfoSchema>;

/** GET /api/v1/dd/{stage}/recent — latest checkpoint thread per slug. */
export const stageRecentSchema = z.looseObject({
  recent: z.array(
    z.looseObject({
      slug: z.string(),
      thread_id: z.string(),
    }),
  ),
});

/** GET /api/v1/dd/{stage}/{slug}/active — live-run registry. A checkpoint
 * with status="running" is not proof of liveness; this is. */
export const stageActiveRunSchema = z.looseObject({
  active: z.boolean(),
  thread_id: z.string().nullable().catch(null),
  started_ts: z.number().nullable().catch(null),
});
export type StageActiveRun = z.infer<typeof stageActiveRunSchema>;

/** GET /api/v1/dd/synth/{slug}/active — study orchestrator variant. */
export const synthActiveRunSchema = z.looseObject({
  active: z.boolean(),
  study_thread_id: z.string().nullable().catch(null),
  chapter_thread_id: z.string().nullable().catch(null),
  started_ts: z.number().nullable().catch(null),
});
export type SynthActiveRun = z.infer<typeof synthActiveRunSchema>;

/** GET /api/v1/dd/{stage}/debug/graph/{thread_id}/state — checkpoint
 * values are open (node-specific KPIs displayed verbatim); `next` is
 * LangGraph's authoritative scheduled-node set. */
export const graphStateSchema = z
  .looseObject({
    values: z.record(z.string(), z.unknown()).catch({}),
    next: z.array(z.string()).catch([]),
    // The planner endpoint calls the scheduled-node list `next_nodes`; the synth endpoint calls it `next`.
    next_nodes: z.array(z.string()).catch([]),
  })
  .transform((state) => ({ ...state, next: state.next.length > 0 ? state.next : state.next_nodes }));
export type GraphState = z.infer<typeof graphStateSchema>;

/** GET /api/v1/dd/planner/{slug}/timing — persisted run wall-clock. */
export const stageTimingSchema = z.looseObject({
  total_wall_ms: z.number().catch(0),
});

/** POST start/resume/cancel responses — locked rejection carries the
 * server's explanatory message; cancel reports propagation. */
export const stageStartResponseSchema = z.looseObject({
  status: z.string().optional(),
  message: z.string().nullable().catch(null),
  stage: z.string().nullable().catch(null),
  slug: z.string().nullable().catch(null),
});

// ============================================================
// Synth — chapter plan + study orchestrator
// ============================================================

/** GET /api/v1/dd/synth/{slug}/study/chapters — plan chapters merged with
 * durable render status (MinIO). `ok` on this endpoint ⇔ a plan exists. */
export const synthChaptersSchema = z.looseObject({
  chapters: z.array(
    z.looseObject({
      id: z.string(),
      title: z.string().nullable().catch(null),
      order: z.number().nullable().catch(null),
      rendered: z.boolean().catch(false),
      wall_ms: z.number().nullable().catch(null),
      thread_id: z.string().nullable().catch(null),
      // What the finished chapter looks like; absent until it has been rendered.
      audit_passed: z.boolean().nullish().catch(null),
      n_sections: z.number().nullish().catch(null),
      n_sources: z.number().nullish().catch(null),
      rendered_chars: z.number().nullish().catch(null),
    }),
  ),
  study_total_wall_ms: z.number().catch(0),
});
export type SynthChapters = z.infer<typeof synthChaptersSchema>;

/** POST /api/v1/dd/synth/{slug} — study run dispatch (ids are upgraded to
 * titles via the chapters endpoint) or single-chapter thread. */
export const synthStartResponseSchema = z.looseObject({
  study_thread_id: z.string().optional(),
  chapter_ids: z.array(z.string()).optional(),
  thread_id: z.string().optional(),
});

/** SSE events on /{stage}/{thread_id}/events — open objects keyed by
 * `step`/`kind`; live-progress payload fields are event-specific. */
export const stageEventSchema = z.looseObject({
  step: z.string().optional(),
  kind: z.string().optional(),
  ts: z.number().optional(),
});
export type StageEvent = z.infer<typeof stageEventSchema>;

// ============================================================
// Usage — LLM call and token counts of a run
// ============================================================

const usageCountsSchema = z.looseObject({
  calls: z.number().catch(0),
  tokens_in: z.number().catch(0),
  tokens_out: z.number().catch(0),
  reasoning_tokens: z.number().catch(0),
});
export type UsageCounts = z.infer<typeof usageCountsSchema>;

/** GET /api/v1/dd/{stage}/debug/graph/{thread_id}/llm-counters — totals and a per-node breakdown. The
 * backend also breaks counts down by model; the UI never shows that, so it is left unparsed. */
export const usagePayloadSchema = z.looseObject({
  total: usageCountsSchema.catch({ calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0 }),
  by_node: z.record(z.string(), usageCountsSchema).catch({}),
});
export type UsagePayload = z.infer<typeof usagePayloadSchema>;

/** GET /api/v1/dd/planner/{slug}/health — how the latest plan was produced. `degraded` is the one-flag
 * answer to "will this plan be any good"; null when there is no plan or it could not be read. */
export const plannerHealthSchema = z.looseObject({
  degraded: z.boolean().nullable().catch(null),
  pipeline_health: z.record(z.string(), z.unknown()).catch({}),
});
export type PlannerHealth = z.infer<typeof plannerHealthSchema>;
