import {
  client,
  deleteBdRagBookIdRunsRunIdIndex,
  getBdRagBookIdRunsRunIdIndex,
  getBdRagCapabilities,
  postBdRagBookIdRunsRunIdIndex,
  postBdRagBookIdRunsRunIdIndexCancel,
} from "@/shared/api";

import { ragCapabilitiesSchema, ragIndexStatusSchema, ragWipeSchema } from "./index-entities";

export async function getRagIndex(bookId: string, runId: string) {
  const result = await getBdRagBookIdRunsRunIdIndex({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return ragIndexStatusSchema.parse(result.data);
}

export async function startRagIndex(bookId: string, runId: string) {
  const result = await postBdRagBookIdRunsRunIdIndex({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return ragIndexStatusSchema.parse(result.data);
}

export async function replayRagIndexNode(bookId: string, runId: string, nodeId: string) {
  const result = await client.request({
    method: "POST",
    url: "/api/v1/bd/rag/{book_id}/runs/{run_id}/index/replay/{node_id}",
    path: { book_id: bookId, run_id: runId, node_id: nodeId },
    throwOnError: true,
  });
  return ragIndexStatusSchema.parse(result.data);
}

export async function getRagCapabilities() {
  const result = await getBdRagCapabilities({ throwOnError: true });
  return ragCapabilitiesSchema.parse(result.data);
}

export async function cancelRagIndex(bookId: string, runId: string) {
  const result = await postBdRagBookIdRunsRunIdIndexCancel({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return ragIndexStatusSchema.parse(result.data);
}

export async function wipeRagIndex(bookId: string, runId: string) {
  const result = await deleteBdRagBookIdRunsRunIdIndex({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return ragWipeSchema.parse(result.data);
}
