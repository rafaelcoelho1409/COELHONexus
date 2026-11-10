/** Book Distiller Agentic RAG — thin, parsed adapters for the BD Ask and Query contracts. */

import { client } from "@/shared/api";
import { agenticUsageSchema, type AgenticUsage } from "../entities";

import {
  type AskUsage,
  branchResponseSchema,
  type HistoryRow,
  historyResponseSchema,
  llmCountersSchema,
  namespaceMapSchema,
  type QueryHistoryEntry,
  queryHistoryResponseSchema,
  querySchemaResponseSchema,
  type RawQueryResult,
  rawQueryResponseSchema,
  type ThreadSummary,
  threadsResponseSchema,
} from "./entities";
import { QUERY_BACKENDS, QUERY_HISTORY_LIMIT, type QueryBackend } from "./params";

export function askStreamUrl(): string {
  return client.buildUrl({ url: "/api/v1/bd/agents/search/stream" });
}

export async function fetchThreadList(bookId: string): Promise<ThreadSummary[]> {
  const result = await client.request({
    method: "GET",
    url: "/api/v1/bd/agents/threads",
    query: { book_id: bookId },
    throwOnError: true,
  });
  return threadsResponseSchema
    .parse(result.data)
    .items.filter((item) => item.thread_id && (!item.book_id || item.book_id === bookId));
}

export async function fetchHistory(threadId: string): Promise<HistoryRow[]> {
  const result = await client.request({
    method: "GET",
    url: "/api/v1/bd/agents/history/{thread_id}",
    path: { thread_id: threadId },
    throwOnError: true,
  });
  return historyResponseSchema.parse(result.data).items;
}

export async function fetchAskUsage(threadId: string): Promise<AskUsage | null> {
  const result = await client.request({
    method: "GET",
    url: "/api/v1/bd/agents/usage/{thread_id}",
    path: { thread_id: threadId },
    throwOnError: true,
  });
  return llmCountersSchema.parse(result.data);
}

export async function fetchPipelineUsage(bookId: string, runId: string): Promise<AgenticUsage> {
  const result = await client.request({
    method: "GET",
    url: "/api/v1/bd/distill/{book_id}/runs/{run_id}/llm-counters",
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return agenticUsageSchema.parse(result.data);
}

export async function cancelAskTurn(turnId: number): Promise<boolean> {
  try {
    await client.request({
      method: "POST",
      url: "/api/v1/bd/agents/turns/{turn_id}/cancel",
      path: { turn_id: turnId },
      throwOnError: true,
    });
    return true;
  } catch {
    return false;
  }
}

export async function branchThread(
  threadId: string,
  upToCreatedAt: string | null,
): Promise<{ newThreadId: string; copied: number } | null> {
  const result = await client.request({
    method: "POST",
    url: "/api/v1/bd/agents/threads/{thread_id}/branch",
    path: { thread_id: threadId },
    body: { up_to_created_at: upToCreatedAt },
    throwOnError: true,
  });
  const data = branchResponseSchema.parse(result.data);
  return data.new_thread_id ? { newThreadId: data.new_thread_id, copied: data.copied } : null;
}

export async function deleteThread(threadId: string): Promise<boolean> {
  try {
    await client.request({
      method: "DELETE",
      url: "/api/v1/bd/agents/threads/{thread_id}",
      path: { thread_id: threadId },
      throwOnError: true,
    });
    return true;
  } catch {
    return false;
  }
}

export async function runRawQuery(backend: QueryBackend, body: string): Promise<RawQueryResult> {
  const result = await client.request({
    method: "POST",
    url: "/api/v1/bd/query/raw/{backend}",
    path: { backend },
    body: { app: "bd", body },
    throwOnError: true,
  });
  return rawQueryResponseSchema.parse(result.data);
}

export async function fetchQueryLabels(): Promise<Record<QueryBackend, string>> {
  const result = await client.request({
    method: "GET",
    url: "/api/v1/bd/query/namespaces",
    throwOnError: true,
  });
  const bd = namespaceMapSchema.parse(result.data).matrix.bd ?? {};
  return {
    elasticsearch: bd.elasticsearch?.label ?? "",
    qdrant: bd.qdrant?.label ?? "",
    neo4j: bd.neo4j?.label ?? "",
  };
}

export async function fetchQuerySchema(backend: QueryBackend): Promise<Record<string, unknown>> {
  const result = await client.request({
    method: "GET",
    url: "/api/v1/bd/query/schema/{backend}",
    path: { backend },
    throwOnError: true,
  });
  return querySchemaResponseSchema.parse(result.data).schema;
}

export async function fetchQueryHistory(backend: QueryBackend): Promise<QueryHistoryEntry[]> {
  const result = await client.request({
    method: "GET",
    url: "/api/v1/bd/query/history",
    query: { backend, app: "bd", limit: QUERY_HISTORY_LIMIT },
    throwOnError: true,
  });
  return queryHistoryResponseSchema
    .parse(result.data)
    .items.filter(
      (item) =>
        item.id > 0 &&
        (!item.app || item.app === "bd") &&
        QUERY_BACKENDS.some((candidate) => candidate.id === item.backend),
    );
}

export async function saveQueryHistory(backend: QueryBackend, body: string, prompt: string): Promise<void> {
  try {
    await client.request({
      method: "POST",
      url: "/api/v1/bd/query/history",
      body: { backend, app: "bd", body, prompt },
      throwOnError: true,
    });
  } catch {
    // Query history is a convenience; a successful data query remains successful when this write fails.
  }
}

export async function deleteQueryHistory(entryId: number): Promise<boolean> {
  try {
    await client.request({
      method: "DELETE",
      url: "/api/v1/bd/query/history/{entry_id}",
      path: { entry_id: entryId },
      throwOnError: true,
    });
    return true;
  } catch {
    return false;
  }
}

export function queryAiUrl(backend: QueryBackend): string {
  return client.buildUrl({ url: "/api/v1/bd/query/ai/{backend}", path: { backend } });
}
