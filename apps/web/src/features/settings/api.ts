/** Settings — the two saved connections through the generated client, one descriptor per kind so the hook
 * never branches on which endpoint it is talking to. Responses stay `unknown` here; the hook parses them. */

import {
  getSettingsEmbedding,
  getSettingsEmbeddingQueryKey,
  getSettingsEndpoint,
  getSettingsEndpointQueryKey,
  postSettingsEmbeddingTest,
  postSettingsEndpointTest,
  putSettingsEmbedding,
  putSettingsEndpoint,
} from "@/shared/api";

import { type EmbeddingTestResult, embeddingTestSchema, type LlmTestResult, llmTestSchema } from "./entities";
import type { ConnectionKind, SaveBody } from "./params";

type ConnectionApi = {
  queryKey: () => readonly unknown[];
  read: (signal: AbortSignal) => Promise<unknown>;
  write: (body: SaveBody) => Promise<unknown>;
  test: () => Promise<unknown>;
  parseTest: (raw: unknown) => LlmTestResult | EmbeddingTestResult;
};

const endpoint: ConnectionApi = {
  queryKey: getSettingsEndpointQueryKey,
  read: async (signal) => (await getSettingsEndpoint({ signal, throwOnError: true })).data,
  write: async (body) => (await putSettingsEndpoint({ body, throwOnError: true })).data,
  test: async () => (await postSettingsEndpointTest({ throwOnError: true })).data,
  parseTest: (raw) => llmTestSchema.parse(raw),
};

const embedding: ConnectionApi = {
  queryKey: getSettingsEmbeddingQueryKey,
  read: async (signal) => (await getSettingsEmbedding({ signal, throwOnError: true })).data,
  write: async (body) => (await putSettingsEmbedding({ body, throwOnError: true })).data,
  test: async () => (await postSettingsEmbeddingTest({ throwOnError: true })).data,
  parseTest: (raw) => embeddingTestSchema.parse(raw),
};

export const CONNECTION_API: Record<ConnectionKind, ConnectionApi> = { llm: endpoint, embedding };
