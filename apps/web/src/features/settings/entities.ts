import { z } from "zod";

/**
 * Wire shapes for the Settings + embedding-migration endpoints, parsed from the generated client's open
 * objects. Responses never contain the raw key — only the masked status (has_key, source, last4).
 */

export const keyStatusSchema = z.object({
  has_key: z.boolean(),
  source: z.enum(["user", "env"]).nullable(),
  last4: z.string().nullable(),
});
export const connectionViewSchema = keyStatusSchema.extend({ url: z.string(), model: z.string() });

export const llmTestSchema = z.discriminatedUnion("ok", [
  z.object({ ok: z.literal(true), reply: z.string(), latency_ms: z.number(), model: z.string().nullish() }),
  z.object({ ok: z.literal(false), error: z.string(), latency_ms: z.number() }),
]);
export const embeddingTestSchema = z.discriminatedUnion("ok", [
  z.object({
    ok: z.literal(true),
    dimensions: z.number(),
    latency_ms: z.number(),
    model: z.string().nullish(),
  }),
  z.object({ ok: z.literal(false), error: z.string(), latency_ms: z.number() }),
]);

const migrationModels = z.object({ from_model: z.string(), to_model: z.string() });
export const migrationStatusSchema = z.object({
  needed: z.boolean(),
  mismatch: migrationModels.nullable(),
  // null when no migration was ever started; otherwise the stored job state
  state: z
    .looseObject({
      status: z.string(), // "running" while a job is in flight
      from_model: z.string(),
      to_model: z.string(),
      task_id: z.string().optional(), // "" until the worker task exists
    })
    .nullable(),
  active_collection: z.string().nullable(),
});
export const taskStatusSchema = z.looseObject({
  task_id: z.string(),
  state: z.string(), // PENDING | STARTED | PROGRESS | SUCCESS | FAILURE | REVOKED
  meta: z.looseObject({ current: z.number().optional(), total: z.number().optional() }).optional(),
  error: z.string().optional(),
});

export type KeyStatus = z.infer<typeof keyStatusSchema>;
export type ConnectionView = z.infer<typeof connectionViewSchema>;
export type LlmTestResult = z.infer<typeof llmTestSchema>;
export type EmbeddingTestResult = z.infer<typeof embeddingTestSchema>;
export type MigrationStatus = z.infer<typeof migrationStatusSchema>;
export type TaskStatus = z.infer<typeof taskStatusSchema>;
