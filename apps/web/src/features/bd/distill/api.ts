import {
  client,
  postBdDistillBookIdRuns,
  postBdDistillBookIdRunsRunIdCancel,
  postBdDistillBookIdRunsRunIdResume,
} from "@/shared/api";
import { distillCancelSchema, distillRunStartSchema } from "./entities";
import { DISTILL_RUN_CONTROLS_ENABLED } from "./params";

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
  assertRunControlsEnabled();
  const result = await postBdDistillBookIdRuns({
    path: { book_id: bookId },
    body: { ingestion_ai_run_id: ingestionAiRunId },
    throwOnError: true,
  });
  return distillRunStartSchema.parse(result.data);
}

export async function resumeDistillRun(bookId: string, runId: string) {
  assertRunControlsEnabled();
  const result = await postBdDistillBookIdRunsRunIdResume({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return distillRunStartSchema.parse(result.data);
}

export async function cancelDistillRun(bookId: string, runId: string) {
  assertRunControlsEnabled();
  const result = await postBdDistillBookIdRunsRunIdCancel({
    path: { book_id: bookId, run_id: runId },
    throwOnError: true,
  });
  return distillCancelSchema.parse(result.data);
}

function assertRunControlsEnabled(): void {
  if (!DISTILL_RUN_CONTROLS_ENABLED) throw new Error("Distill run controls are disabled.");
}
