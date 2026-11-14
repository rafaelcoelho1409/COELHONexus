/** Book Distiller — zod schemas for streamed events, upload answers, and agentic lifecycle responses not yet typed by
 * the generated client. Every field the UI relies on has a fallback, so malformed stream data never breaks the view. */

import { z } from "zod";
import type { Snapshot } from "@/shared/api";

const count = z.number().catch(0);

const statsSchema = z.looseObject({
  chars: count,
  headings: count,
  tables: count,
  pictures: count,
  formulas: count,
});
export type PageStats = z.infer<typeof statsSchema>;

export const pageBodySchema = z.object({
  book_id: z.string(),
  page: z.number(),
  markdown: z.string(),
  content_source: z.enum(["docling", "agentic"]).catch("docling"),
});

export const contentSelectionPageSchema = z.looseObject({
  page_number: z.number().int().positive(),
  role: z.enum(["core", "preface", "reference", "navigation", "legal", "noise", "unknown"]),
  confidence: z.number().min(0).max(1),
  rationale: z.string(),
  disposition: z.enum(["include", "exclude"]),
  decision_source: z.enum(["classifier", "content_policy", "conservative_fallback", "user"]),
});
export const contentSelectionSchema = z.looseObject({
  book_id: z.string(),
  extraction_run_id: z.string(),
  agentic_run_id: z.string(),
  classifier_version: z.string(),
  revision: z.number().int().positive(),
  expected_page_count: z.number().int().positive(),
  included_page_count: z.number().int().nonnegative(),
  excluded_page_count: z.number().int().nonnegative(),
  sha256: z.string(),
  pages: z.array(contentSelectionPageSchema),
});
export type ContentSelection = z.infer<typeof contentSelectionSchema>;
export type ContentSelectionPage = z.infer<typeof contentSelectionPageSchema>;

export const processedBookPageSchema = z.object({
  processed_page_number: z.number().int().positive(),
  source_page_number: z.number().int().positive(),
  section_path: z.array(z.string()).catch([]),
  role: z.string(),
});
export const processedContentsEntrySchema = z.object({
  level: z.number().int().min(1).max(8),
  title: z.string(),
  processed_page_number: z.number().int().positive(),
  source_page_number: z.number().int().positive(),
  confidence: z.number().min(0).max(1),
  review_required: z.boolean(),
});
export const processedBookSchema = z.object({
  book_id: z.string(),
  extraction_run_id: z.string(),
  agentic_run_id: z.string(),
  selection_revision: z.number().int().positive(),
  expected_source_page_count: z.number().int().positive(),
  source_page_count: z.number().int().positive(),
  contents_status: z.enum(["complete", "needs_review", "unavailable"]),
  contents: z.array(processedContentsEntrySchema).catch([]),
  pages: z.array(processedBookPageSchema),
});
export type ProcessedBook = z.infer<typeof processedBookSchema>;

/** What the stored reader layout holds. Every field falls back on its own, so a value from an older version or a
 * hand-edited one still gives a usable layout. */
export const readerLayoutSchema = z.object({
  /** The page's left column (chapters and pages) and right column ("On this page"). */
  contents: z.boolean().catch(true),
  outline: z.boolean().catch(true),
  /** The same two columns inside the reading window, which opens with both closed so the text has the screen. */
  readingContents: z.boolean().catch(false),
  readingOutline: z.boolean().catch(false),
  /** How wide the text runs in the reading window. */
  width: z.enum(["comfortable", "wide"]).catch("comfortable"),
});
export type ReaderLayout = z.infer<typeof readerLayoutSchema>;

const patchSuggestionSchema = z.object({
  target_text: z.string(),
  replacement: z.string(),
  rationale: z.string().catch(""),
  confidence: z.number().catch(0),
  risk: z.string().catch(""),
});

const pageIssueSchema = z.object({
  issue_id: z.string(),
  code: z.string(),
  severity: z.string().catch(""),
  description: z.string().catch(""),
  evidence: z.array(z.string()).catch([]),
  resolution: z.enum(["auto_applied", "review", "none"]).catch("none"),
  proposal: patchSuggestionSchema.nullable().catch(null),
});
/** What the current Agentic AI run found on one page and what it did about it (`GET …/pages/{n}/quality`). */
export const pageQualitySchema = z.object({
  book_id: z.string(),
  agentic_run_id: z.string(),
  page: z.number(),
  page_kind: z.string().catch("text"),
  status: z.enum(["done", "review_required"]).catch("review_required"),
  delivery_disposition: z.enum(["include", "discarded_blank", "image_only"]).catch("include"),
  issues: z.array(pageIssueSchema).catch([]),
  mechanical_fixes: z.record(z.string(), z.number()).catch({}),
  audit_warnings: z.array(z.string()).catch([]),
  errors: z.array(z.string()).catch([]),
});
export type PageQuality = z.infer<typeof pageQualitySchema>;

const agenticProgressSchema = z.looseObject({
  current_node: z.string().nullable().catch(null),
  node_status: z.record(z.string(), z.enum(["pending", "running", "done", "failed"])).catch({}),
  audited_pages: z.array(z.number()).catch([]),
  audit_warning_pages: z.array(z.number()).catch([]),
  processed_pages: z.array(z.number()).catch([]),
  active_audit_pages: z.array(z.number()).catch([]),
  active_repair_pages: z.array(z.number()).catch([]),
  classified_pages: z.array(z.number()).catch([]).optional(),
  active_classification_pages: z.array(z.number()).catch([]).optional(),
});

export const agenticRunStatusSchema = z.looseObject({
  book_id: z.string(),
  extraction_run_id: z.string(),
  agentic_run_id: z.string(),
  status: z.enum(["queued", "running", "done", "failed", "cancelled"]),
  expected_page_count: z.number().catch(0),
  published_page_count: z.number().catch(0),
  quality_status: z.enum(["complete", "complete_with_review"]).nullable().catch(null),
  delivered_page_count: z.number().catch(0),
  discarded_blank_page_count: z.number().catch(0),
  selected_page_count: z.number().catch(0),
  excluded_page_count: z.number().catch(0),
  content_selection_revision: z.number().nullable().catch(null),
  processed_page_count: z.number().catch(0),
  processed_book_revision: z.number().nullable().catch(null),
  contents_status: z.enum(["complete", "needs_review", "unavailable"]).nullable().catch(null),
  review_page_count: z.number().catch(0),
  review_pages: z.array(z.number()).catch([]),
  unverified_page_count: z.number().catch(0),
  unverified_pages: z.array(z.number()).catch([]),
  discarded_pages: z.array(z.number()).catch([]),
  image_only_pages: z.array(z.number()).catch([]),
  progress: agenticProgressSchema,
  created_at: z.number().catch(0),
  updated_at: z.number().catch(0),
  finished_at: z.number().nullable().catch(null),
  error: z.string().nullable().catch(null),
});
export type AgenticRunStatus = z.infer<typeof agenticRunStatusSchema>;
export type AgenticProgress = z.infer<typeof agenticProgressSchema>;
export type BookSnapshot = Snapshot & { agentic: AgenticRunStatus | null };

export const agenticRunStartSchema = z.object({
  status: z.literal("queued"),
  book_id: z.string(),
  extraction_run_id: z.string(),
  agentic_run_id: z.string(),
});
export type AgenticRunStart = z.infer<typeof agenticRunStartSchema>;

export const agenticRunCancelResponseSchema = z.object({
  book_id: z.string(),
  agentic_run_id: z.string(),
  status: z.enum(["cancelling", "cancelled", "done", "failed"]),
});
export type AgenticRunCancelResponse = z.infer<typeof agenticRunCancelResponseSchema>;

export const agenticRunWipeResponseSchema = z.object({
  book_id: z.string(),
  agentic_run_id: z.string(),
  wiped: z.boolean(),
  objects_deleted: count,
});
export type AgenticRunWipeResponse = z.infer<typeof agenticRunWipeResponseSchema>;

export const agenticGraphSchema = z.looseObject({ graph_mermaid: z.string() });
export type AgenticGraph = z.infer<typeof agenticGraphSchema>;

export const extractionWipeResponseSchema = z.object({
  book_id: z.string(),
  wiped: z.boolean(),
  objects_deleted: count,
});
export type ExtractionWipeResponse = z.infer<typeof extractionWipeResponseSchema>;

export const agenticEventSchema = z.looseObject({
  kind: z.enum([
    "run_queued",
    "run_cancelling",
    "node",
    "page_audit",
    "page_repair",
    "content_classification",
    "run_done",
    "run_failed",
    "run_cancelled",
  ]),
  ts: z.number().optional(),
  node: z.string().optional(),
  status: z.string().optional(),
  page: z.number().optional(),
  progress: agenticProgressSchema.optional(),
  error: z.string().nullable().optional(),
});
export type AgenticEvent = z.infer<typeof agenticEventSchema>;

const usageCountsSchema = z.looseObject({
  calls: count,
  tokens_in: count,
  tokens_out: count,
  reasoning_tokens: count,
  errors: count,
});
const modelCountsSchema = z.looseObject({
  calls: count,
  tokens_in: count,
  tokens_out: count,
  reasoning_tokens: count,
});
const nodeUsageSchema = usageCountsSchema.extend({
  by_model: z.record(z.string(), modelCountsSchema).catch({}),
});
export const agenticUsageSchema = z.looseObject({
  total: usageCountsSchema,
  by_node: z.record(z.string(), nodeUsageSchema).catch({}),
});
export type AgenticUsage = z.infer<typeof agenticUsageSchema>;

const ZERO_STATS: PageStats = { chars: 0, headings: 0, tables: 0, pictures: 0, formulas: 0 };

const outlineEntrySchema = z.looseObject({
  level: z.number().catch(1),
  title: z.string().catch(""),
  page: z.number(),
});
export type OutlineEntry = z.infer<typeof outlineEntrySchema>;

const chapterSchema = z.looseObject({
  title: z.string().catch(""),
  first_page: z.number(),
  last_page: z.number(),
});
export type ChapterRange = z.infer<typeof chapterSchema>;

const startedSchema = z.looseObject({
  kind: z.literal("started"),
  title: z.string().catch(""),
  n_pages: z.number().catch(0),
  outline: z.array(outlineEntrySchema).catch([]),
  chapters: z.array(chapterSchema).catch([]),
  resumed: z.boolean().catch(false),
  completed: z.number().catch(0),
});

const pageSchema = z.looseObject({
  kind: z.literal("page"),
  page: z.number(),
  markdown: z.string().catch(""),
  /** The server cut the text for the stream; the full content unit is `GET …/pages/{n}`. */
  truncated: z.boolean().catch(false),
  stats: statsSchema.catch(ZERO_STATS),
  figures: z.array(z.string()).catch([]),
  section_path: z.array(z.string()).catch([]),
  completed: z.number().catch(0),
  total: z.number().catch(0),
});

const sliceDoneSchema = z.looseObject({
  kind: z.literal("slice_done"),
  first: z.number(),
  last: z.number(),
  seconds: z.number().catch(0),
});

function terminalSchema<K extends "done" | "failed" | "cancelled">(kind: K) {
  return z.looseObject({
    kind: z.literal(kind),
    pages: z.number().catch(0),
    total: z.number().catch(0),
    error: z.string().nullable().catch(null),
  });
}

export const bdEventSchema = z.discriminatedUnion("kind", [
  startedSchema,
  pageSchema,
  sliceDoneSchema,
  terminalSchema("done"),
  terminalSchema("failed"),
  terminalSchema("cancelled"),
]);
export type BdEvent = z.infer<typeof bdEventSchema>;
export type TerminalEvent = Extract<BdEvent, { kind: "done" | "failed" | "cancelled" }>;

/** After a terminal event the stream is over (the server keeps the connection open until the client closes it). */
export function isTerminalEvent(event: BdEvent): event is TerminalEvent {
  return event.kind === "done" || event.kind === "failed" || event.kind === "cancelled";
}

/** POST /api/v1/bd/ingestion — read by hand (XHR, for the upload progress), so parsed here. */
export const startResponseSchema = z.object({
  status: z.enum(["queued", "cached", "locked"]),
  book_id: z.string(),
  run_id: z.string().nullable().catch(null),
  resumed: z.boolean().catch(false),
});
export type StartResponse = z.infer<typeof startResponseSchema>;
