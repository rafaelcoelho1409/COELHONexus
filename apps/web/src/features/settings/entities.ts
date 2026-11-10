import { z } from "zod";

import { SEARCH_PROVIDER_IDS } from "./params";

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

export const searchProviderSchema = z.looseObject({
  provider_id: z.enum(SEARCH_PROVIDER_IDS),
  name: z.string(),
  endpoint: z.string().nullable(),
  account_url: z.string().url().nullable(),
  available: z.boolean(),
  key_required: z.boolean(),
  has_key: z.boolean(),
  key_source: z.enum(["user", "env"]).nullable(),
  key_last4: z.string().nullable(),
  connected: z.boolean(),
  last_checked_at: z.number().nullable(),
  tools: z.array(z.string()),
  last_test_error: z.string().nullable(),
});
export const searchSettingsSchema = z.looseObject({
  default_provider: z.enum(SEARCH_PROVIDER_IDS).nullable(),
  providers: z.array(searchProviderSchema),
});
export const searchTestSchema = z.looseObject({
  ok: z.boolean(),
  provider_id: z.enum(SEARCH_PROVIDER_IDS),
  latency_ms: z.number().int().nonnegative(),
  tools: z.array(z.string()),
  error: z.string().nullable(),
});

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
export type SearchProvider = z.infer<typeof searchProviderSchema>;
export type SearchSettings = z.infer<typeof searchSettingsSchema>;
export type SearchTestResult = z.infer<typeof searchTestSchema>;
export type LlmTestResult = z.infer<typeof llmTestSchema>;
export type EmbeddingTestResult = z.infer<typeof embeddingTestSchema>;
export type MigrationStatus = z.infer<typeof migrationStatusSchema>;
export type TaskStatus = z.infer<typeof taskStatusSchema>;
