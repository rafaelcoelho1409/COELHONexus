/** Book Distiller Agentic RAG — runtime schemas for Ask streams and the Query workbench. */

import { z } from "zod";

const modelCountsSchema = z.object({
  calls: z.number().catch(0),
  tokens_in: z.number().catch(0),
  tokens_out: z.number().catch(0),
  reasoning_tokens: z.number().catch(0),
});
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
  by_node: z
    .record(z.string(), z.looseObject({ by_model: z.record(z.string(), modelCountsSchema).catch({}) }))
    .catch({}),
});
export type AskUsage = z.infer<typeof llmCountersSchema>;

const threadSummarySchema = z.looseObject({
  thread_id: z.string().catch(""),
  book_id: z.string().catch(""),
  first_question: z.string().catch(""),
  turn_count: z.number().int().catch(0),
  last_seen: z.string().catch(""),
});
export type ThreadSummary = z.infer<typeof threadSummarySchema>;

export const threadsResponseSchema = z.looseObject({
  items: z.array(threadSummarySchema).catch([]),
});

/** One cited page or learning unit. `page` is optional for EPUB sections and synthesized study material. */
const citationSchema = z.looseObject({
  title: z.string().catch(""),
  url: z.string().catch(""),
  book_id: z.string().catch(""),
  page: z.number().int().positive().nullish().catch(null),
  chapter: z.string().catch(""),
  section: z.string().catch(""),
  snippet: z.string().catch(""),
  source: z.string().catch(""),
});
export type AskCitation = z.infer<typeof citationSchema>;

const askSubQuestionSchema = z.looseObject({
  question: z.string().catch(""),
  status: z.string().catch("queued"),
  answer: z.string().nullish().catch(null),
  answer_preview: z.string().nullish().catch(null),
});

const subResultSchema = z.looseObject({
  sub_question: z.string().catch(""),
  answer: z.string().catch(""),
});

const askGraphStateSchema = z.looseObject({
  route: z.string().catch(""),
  active_node: z.string().nullish().catch(null),
  visited_nodes: z.array(z.string()).catch([]),
  retrieval_sources: z.array(z.string()).catch([]),
  status: z.enum(["idle", "running", "done", "failed"]).catch("idle"),
});

const thinkingStateSchema = z.looseObject({
  stages: z
    .record(z.string(), z.looseObject({ status: z.string().catch(""), action: z.string().catch("") }))
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
  graph: askGraphStateSchema.nullish().catch(null),
  book_id: z.string().nullish().catch(null),
  citations: z.array(citationSchema).nullish().catch(null),
  duration_ms: z.number().nullish().catch(null),
  usage: llmCountersSchema.nullish().catch(null),
  _seq: z.number().nullish().catch(null),
});
export type AskThinkingState = z.infer<typeof thinkingStateSchema>;

const historyRowSchema = z.looseObject({
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

export const askEventSchema = z.looseObject({
  node: z.string().catch("unknown"),
  turn_id: z.number().nullish().catch(null),
  mode: z.string().nullish().catch(null),
  graph: askGraphStateSchema.nullish().catch(null),
  retrieval_sources: z.array(z.string()).nullish().catch(null),
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

const rawHitSchema = z.looseObject({
  summary: z.string().catch(""),
  raw: z.record(z.string(), z.unknown()).catch({}),
});
export type RawHit = z.infer<typeof rawHitSchema>;

export const rawQueryResponseSchema = z.looseObject({
  ok: z.boolean().catch(false),
  error: z.string().nullish().catch(null),
  notes: z.array(z.string()).catch([]),
  took_ms: z.number().catch(0),
  total: z.number().nullish().catch(null),
  hits: z.array(rawHitSchema).catch([]),
});
export type RawQueryResult = z.infer<typeof rawQueryResponseSchema>;

const namespaceEntrySchema = z.looseObject({
  available: z.boolean().catch(false),
  label: z.string().catch(""),
});

export const namespaceMapSchema = z.looseObject({
  matrix: z.record(z.string(), z.record(z.string(), namespaceEntrySchema)).catch({}),
});

export const querySchemaResponseSchema = z.looseObject({
  schema: z.record(z.string(), z.unknown()).catch({}),
});

const queryHistoryEntrySchema = z.looseObject({
  id: z.number().catch(0),
  backend: z.string().catch(""),
  app: z.string().catch(""),
  body: z.string().catch(""),
  prompt: z.string().catch(""),
  created_at: z.string().catch(""),
});
export type QueryHistoryEntry = z.infer<typeof queryHistoryEntrySchema>;

export const queryHistoryResponseSchema = z.looseObject({
  items: z.array(queryHistoryEntrySchema).catch([]),
});

export const queryAiEventSchema = z.looseObject({
  event: z.string().catch("unknown"),
  data: z.string().nullish().catch(null),
  ok: z.boolean().nullish().catch(null),
  final: z.string().nullish().catch(null),
  error: z.string().nullish().catch(null),
});
export type QueryAiEvent = z.infer<typeof queryAiEventSchema>;

export const branchResponseSchema = z.looseObject({
  new_thread_id: z.string().catch(""),
  copied: z.number().int().catch(0),
});
