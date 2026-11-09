import { z } from "zod";

export const ragIndexStatusSchema = z.object({
  book_id: z.string(),
  distill_run_id: z.string(),
  extraction_run_id: z.string(),
  status: z.enum(["not_started", "queued", "running", "done", "failed", "cancelled"]),
  vector_status: z.enum(["pending", "running", "done", "failed", "cancelled"]).catch("pending"),
  graph_status: z.enum(["pending", "running", "done", "failed", "cancelled"]).catch("pending"),
  current_stage: z
    .enum(["preparing", "vector_index", "graph_materialization", "complete"])
    .nullable()
    .catch(null),
  cancel_requested: z.boolean().catch(false),
  attempt: z.number().int().nonnegative().catch(0),
  collection_name: z.string().nullable().catch(null),
  embedding_model: z.string().nullable().catch(null),
  embedding_dimensions: z.number().int().positive().nullable().catch(null),
  source_page_count: z.number().int().nonnegative().catch(0),
  learning_unit_count: z.number().int().nonnegative().catch(0),
  guide_record_count: z.number().int().nonnegative().catch(0),
  point_count: z.number().int().nonnegative().catch(0),
  graph_node_count: z.number().int().nonnegative().catch(0),
  graph_relationship_count: z.number().int().nonnegative().catch(0),
  created_at: z.number().nullable().catch(null),
  updated_at: z.number().nullable().catch(null),
  finished_at: z.number().nullable().catch(null),
  error: z.string().nullable().catch(null),
});
export type RagIndexStatus = z.infer<typeof ragIndexStatusSchema>;

export const ragCapabilitiesSchema = z.object({ execution_enabled: z.boolean() });
export const ragWipeSchema = z.object({
  book_id: z.string(),
  distill_run_id: z.string(),
  wiped: z.boolean(),
  objects_deleted: z.number().int().nonnegative(),
});
