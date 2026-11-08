import {
  client,
  getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelection,
  getBdDistillBookIdRunsRunIdRagIndex,
  deleteBdDistillBookIdRunsRunId,
  postBdDistillBookIdRuns,
  postBdDistillBookIdRunsRunIdCancel,
  postBdDistillBookIdRunsRunIdResume,
  postBdDistillBookIdRunsRunIdRagIndex,
  putBdIngestionBookIdAgenticRunsAgenticRunIdContentSelection,
} from "@/shared/api";
import {
  contentSelectionSchema,
  distillCancelSchema,
  distillRunStartSchema,
  distillWipeSchema,
  ragIndexStatusSchema,
} from "./entities";

export function distillEventsUrl(bookId: string, runId: string): string {
  return client.buildUrl({
    url: "/api/v1/bd/distill/{book_id}/runs/{run_id}/events",
    path: { book_id: bookId, run_id: runId },
  });
}

export function learningGuideHtmlUrl(bookId: string, runId: string): string {
  return client.buildUrl({
    url: "/api/v1/bd/distill/{book_id}/runs/{run_id}/report.html",
    path: { book_id: bookId, run_id: runId },
  });
}

export function learningGuidePdfUrl(bookId: string, runId: string): string {
  return client.buildUrl({
    url: "/api/v1/bd/distill/{book_id}/runs/{run_id}/report.pdf",
    path: { book_id: bookId, run_id: runId },
  });
}

export async function startDistillRun(bookId: string, ingestionAiRunId: string) {
  const result = await postBdDistillBookIdRuns({
    path: { book_id: bookId },
    body: { ingestion_ai_run_id: ingestionAiRunId },
    throwOnError: true,
  });
  return distillRunStartSchema.parse(result.data);
}

export async function resumeDistillRun(bookId: string, runId: string) {
  const result = await postBdDistillBookIdRunsRunIdResume({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return distillRunStartSchema.parse(result.data);
}

export async function cancelDistillRun(bookId: string, runId: string) {
  const result = await postBdDistillBookIdRunsRunIdCancel({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return distillCancelSchema.parse(result.data);
}

export async function wipeDistillRun(bookId: string, runId: string) {
  const result = await deleteBdDistillBookIdRunsRunId({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return distillWipeSchema.parse(result.data);
}

export async function getDistillRagIndex(bookId: string, runId: string) {
  const result = await getBdDistillBookIdRunsRunIdRagIndex({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return ragIndexStatusSchema.parse(result.data);
}

export async function retryDistillRagIndex(bookId: string, runId: string) {
  const result = await postBdDistillBookIdRunsRunIdRagIndex({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return ragIndexStatusSchema.parse(result.data);
}

export async function getContentSelection(bookId: string, agenticRunId: string) {
  const result = await getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelection({
    path: { book_id: bookId, agentic_run_id: agenticRunId },
    throwOnError: true,
  });
  return contentSelectionSchema.parse(result.data);
}

export async function saveContentSelection(
  bookId: string,
  agenticRunId: string,
  expectedRevision: number,
  overrides: { page_number: number; disposition: "include" | "exclude" }[],
) {
  const result = await putBdIngestionBookIdAgenticRunsAgenticRunIdContentSelection({
    path: { book_id: bookId, agentic_run_id: agenticRunId },
    body: { expected_revision: expectedRevision, overrides },
    throwOnError: true,
  });
  return contentSelectionSchema.parse(result.data);
}
