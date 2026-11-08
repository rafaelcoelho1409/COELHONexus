import { z } from "zod";

const evidenceQuoteSchema = z.looseObject({
  page_number: z.number().int().positive(),
  quote: z.string(),
});

const sourceRefSchema = z.looseObject({
  source_type: z.enum(["book", "agent_inference"]),
  book_id: z.string(),
  extraction_run_id: z.string(),
  ingestion_ai_run_id: z.string(),
  chunk_id: z.string(),
  source_key: z.string(),
  page_number: z.number().int().positive(),
  section_path: z.array(z.string()).catch([]),
  source_sha256: z.string(),
  quote: z.string(),
});
export type SourceRef = z.infer<typeof sourceRefSchema>;

const reportLessonSchema = z.looseObject({
  title: z.string(),
  explanation: z.string(),
  source_refs: z.array(sourceRefSchema),
  attribution: z.enum(["book", "agent_inference"]).catch("book"),
  verification_status: z.enum(["verified", "unverified"]).catch("unverified"),
  confidence: z.number().min(0).max(1).catch(0),
});
export type ReportLesson = z.infer<typeof reportLessonSchema>;

const diagramNodeSchema = z.looseObject({
  node_id: z.string(),
  label: z.string(),
  attribution: z.enum(["book", "agent_inference"]).catch("book"),
  learning_unit_ids: z.array(z.string()).catch([]),
  evidence: z.array(evidenceQuoteSchema).catch([]),
});

const diagramEdgeSchema = z.looseObject({
  source_id: z.string(),
  target_id: z.string(),
  relation: z.enum(["supports", "requires", "defines", "contrasts", "sequence", "example_of"]),
  attribution: z.enum(["book", "agent_inference"]).catch("book"),
  learning_unit_ids: z.array(z.string()).catch([]),
  evidence: z.array(evidenceQuoteSchema).catch([]),
});

const diagramSchema = z.looseObject({
  title: z.string(),
  accessible_description: z.string(),
  nodes: z.array(diagramNodeSchema),
  edges: z.array(diagramEdgeSchema),
});
export type ReportDiagram = z.infer<typeof diagramSchema>;

const equationSchema = z.looseObject({
  title: z.string(),
  latex: z.string(),
  explanation: z.string(),
  attribution: z.enum(["book", "agent_inference"]).catch("book"),
  assumptions: z.array(z.string()).catch([]),
  learning_unit_ids: z.array(z.string()).catch([]),
  evidence: z.array(evidenceQuoteSchema).catch([]),
});

const dataColumnSchema = z.looseObject({
  name: z.string(),
  value_type: z.enum(["number", "text"]),
  unit: z.string().nullable().optional(),
});

const datasetRowSchema = z.looseObject({
  values: z.record(z.string(), z.union([z.string(), z.number().finite(), z.null()])),
  learning_unit_ids: z.array(z.string()).catch([]),
  evidence: z.array(evidenceQuoteSchema).catch([]),
});

const datasetSchema = z.looseObject({
  dataset_id: z.string(),
  title: z.string(),
  columns: z.array(dataColumnSchema),
  rows: z.array(datasetRowSchema),
  transformations: z.array(z.string()).catch([]),
  assumptions: z.array(z.string()).catch([]),
  learning_unit_ids: z.array(z.string()).catch([]),
});

const chartSchema = z.looseObject({
  title: z.string(),
  chart_type: z.enum(["bar", "line", "scatter", "table"]),
  x_field: z.string(),
  y_fields: z.array(z.string()),
  caption: z.string(),
  accessible_description: z.string(),
  learning_unit_ids: z.array(z.string()).catch([]),
  dataset: datasetSchema,
});
export type ReportChart = z.infer<typeof chartSchema>;

const reportSectionSchema = z.looseObject({
  title: z.string(),
  overview: z.string(),
  overview_attribution: z.enum(["book", "agent_inference"]).catch("book"),
  overview_source_refs: z.array(sourceRefSchema),
  lessons: z.array(reportLessonSchema).catch([]),
  open_questions: z.array(reportLessonSchema).catch([]),
  diagrams: z.array(diagramSchema).catch([]),
  equations: z.array(equationSchema).catch([]),
  charts: z.array(chartSchema).catch([]),
});
export type ReportSection = z.infer<typeof reportSectionSchema>;

const structuralQualitySchema = z.looseObject({
  expected_page_count: z.number().int().nonnegative().catch(0),
  selected_page_count: z.number().int().nonnegative().catch(0),
  excluded_page_count: z.number().int().nonnegative().catch(0),
  substantive_page_count: z.number().int().nonnegative().catch(0),
  covered_substantive_pages: z.number().int().nonnegative().catch(0),
  page_coverage_rate: z.number().min(0).max(1).catch(0),
  source_reference_count: z.number().int().nonnegative().catch(0),
  valid_source_reference_count: z.number().int().nonnegative().catch(0),
  evidence_quote_count: z.number().int().nonnegative().catch(0),
  supported_evidence_quote_count: z.number().int().nonnegative().catch(0),
  unverified_lesson_count: z.number().int().nonnegative().catch(0),
  uncovered_substantive_pages: z.array(z.number().int().positive()).catch([]),
  citation_integrity_passed: z.boolean().catch(false),
  quote_support_passed: z.boolean().catch(false),
  errors: z.array(z.string()).catch([]),
});

const progressSchema = z.looseObject({
  current_node: z.string().nullable().catch(null),
  node_status: z.record(z.string(), z.string()).catch({}),
  completed_units: z.number().int().nonnegative().catch(0),
  total_units: z.number().int().nonnegative().catch(0),
  completed_chapters: z.number().int().nonnegative().catch(0),
  total_chapters: z.number().int().nonnegative().catch(0),
});

export const distillRunSchema = z.looseObject({
  schema_version: z.string().optional(),
  workflow_version: z.string().optional(),
  prompt_version: z.string().optional(),
  renderer_version: z.string().optional(),
  run_id: z.string().min(1),
  book_id: z.string().min(1),
  extraction_run_id: z.string().min(1),
  ingestion_ai_run_id: z.string().min(1),
  expected_page_count: z.number().int().positive().catch(0),
  selected_page_count: z.number().int().nonnegative().optional(),
  content_selection_revision: z.number().int().positive().optional(),
  status: z.enum(["queued", "running", "done", "failed", "cancelled"]),
  progress: progressSchema,
  structural_quality: structuralQualitySchema.nullable().catch(null),
  covered_page_count: z.number().int().nonnegative().catch(0),
  uncovered_pages: z.array(z.number().int().positive()).catch([]),
  review_required: z.boolean().catch(true),
  created_at: z.number().catch(0),
  updated_at: z.number().catch(0),
  finished_at: z.number().nullable().catch(null),
  error: z.string().nullable().catch(null),
});
export type DistillRun = z.infer<typeof distillRunSchema>;

export const distillRunListSchema = z.array(distillRunSchema);

export const distillRunStartSchema = z.object({
  status: z.literal("queued"),
  book_id: z.string(),
  extraction_run_id: z.string(),
  ingestion_ai_run_id: z.string(),
  run_id: z.string(),
});
export type DistillRunStart = z.infer<typeof distillRunStartSchema>;

export const distillCancelSchema = z.looseObject({
  book_id: z.string(),
  run_id: z.string(),
  status: z.enum(["queued", "running", "done", "failed", "cancelled"]),
});

export const distillCapabilitiesSchema = z.object({
  execution_enabled: z.boolean(),
});

export const distillWipeSchema = z.object({
  book_id: z.string(),
  run_id: z.string(),
  wiped: z.boolean(),
  objects_deleted: z.number().int().nonnegative(),
});

export const distillEventSchema = z.looseObject({
  kind: z.string(),
  status: z.string().optional(),
  node: z.string().optional(),
  error: z.string().nullable().optional(),
  progress: progressSchema.optional(),
});
export type DistillEvent = z.infer<typeof distillEventSchema>;

export const learningGuideSchema = z.looseObject({
  schema_version: z.string().catch("1.0"),
  title: z.string().min(1),
  book_id: z.string().min(1),
  extraction_run_id: z.string().min(1),
  ingestion_ai_run_id: z.string().min(1),
  distill_run_id: z.string().min(1),
  source_page_count: z.number().int().nonnegative().catch(0),
  selected_page_count: z.number().int().nonnegative().catch(0),
  excluded_page_count: z.number().int().nonnegative().catch(0),
  content_selection_revision: z.number().int().positive().catch(1),
  generated_at: z.number().positive(),
  report_scope: z.literal("book_only").catch("book_only"),
  central_thesis: z.string(),
  central_thesis_attribution: z.enum(["book", "agent_inference"]).catch("book"),
  central_thesis_source_refs: z.array(sourceRefSchema).min(1),
  key_takeaways: z.array(reportLessonSchema).catch([]),
  cross_chapter_connections: z.array(reportLessonSchema).catch([]),
  chapters: z.array(reportSectionSchema),
  glossary: z
    .array(
      z.looseObject({
        term: z.string(),
        definition: z.string(),
        attribution: z.enum(["book", "agent_inference"]).catch("book"),
        source_refs: z.array(sourceRefSchema).catch([]),
        verification_status: z.enum(["verified", "unverified"]).catch("unverified"),
      }),
    )
    .catch([]),
  limitation_notes: z.array(reportLessonSchema).catch([]),
  review_notes: z.array(z.string()).catch([]),
  diagrams: z.array(diagramSchema).catch([]),
  equations: z.array(equationSchema).catch([]),
  charts: z.array(chartSchema).catch([]),
  covered_pages: z.array(z.number().int().positive()).catch([]),
  uncovered_pages: z.array(z.number().int().positive()).catch([]),
  review_required: z.boolean(),
});
export type LearningGuide = z.infer<typeof learningGuideSchema>;

export const contentSelectionPageSchema = z.object({
  page_number: z.number().int().positive(),
  role: z.enum(["core", "preface", "reference", "navigation", "legal", "noise", "unknown"]),
  confidence: z.number().min(0).max(1),
  rationale: z.string(),
  disposition: z.enum(["include", "exclude"]),
  decision_source: z.enum(["classifier", "content_policy", "conservative_fallback", "user"]),
});
export type ContentSelectionPage = z.infer<typeof contentSelectionPageSchema>;

export const contentSelectionSchema = z.object({
  book_id: z.string().min(1),
  extraction_run_id: z.string().min(1),
  agentic_run_id: z.string().min(1),
  classifier_version: z.string().min(1),
  revision: z.number().int().positive(),
  expected_page_count: z.number().int().positive(),
  included_page_count: z.number().int().nonnegative(),
  excluded_page_count: z.number().int().nonnegative(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  pages: z.array(contentSelectionPageSchema),
});
export type ContentSelection = z.infer<typeof contentSelectionSchema>;

export const ragIndexStatusSchema = z.object({
  book_id: z.string(),
  distill_run_id: z.string(),
  extraction_run_id: z.string(),
  status: z.enum(["not_started", "queued", "running", "done", "failed"]),
  attempt: z.number().int().nonnegative().catch(0),
  collection_name: z.string().nullable().catch(null),
  embedding_model: z.string().nullable().catch(null),
  embedding_dimensions: z.number().int().positive().nullable().catch(null),
  source_page_count: z.number().int().nonnegative().catch(0),
  learning_unit_count: z.number().int().nonnegative().catch(0),
  guide_record_count: z.number().int().nonnegative().catch(0),
  point_count: z.number().int().nonnegative().catch(0),
  created_at: z.number().nullable().catch(null),
  updated_at: z.number().nullable().catch(null),
  finished_at: z.number().nullable().catch(null),
  error: z.string().nullable().catch(null),
});
export type RagIndexStatus = z.infer<typeof ragIndexStatusSchema>;
