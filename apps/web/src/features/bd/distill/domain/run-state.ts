import type { BookSnapshot } from "../../entities";
import type { ContentSelection, DistillEvent, DistillRun, DistillRunStart } from "../entities";

export type DistillStatus = DistillRun["status"];

export function isDistillLive(status: DistillStatus): boolean {
  return status === "queued" || status === "running";
}

export function isDistillTerminalEvent(event: DistillEvent): boolean {
  return event.kind === "run_done" || event.kind === "run_failed" || event.kind === "run_cancelled";
}

export function latestDistillRun(runs: readonly DistillRun[]): DistillRun | null {
  return (
    [...runs].sort(
      (left, right) => right.created_at - left.created_at || right.run_id.localeCompare(left.run_id),
    )[0] ?? null
  );
}

export function selectDistillRun(runs: readonly DistillRun[], runId: string | null): DistillRun | null {
  if (runId) {
    const selected = runs.find((run) => run.run_id === runId);
    if (selected) return selected;
  }
  return latestDistillRun(runs);
}

export type DistillReadiness = {
  ready: boolean;
  reason: string;
  steps: {
    id: "extraction" | "quality" | "selection";
    label: string;
    status: "complete" | "waiting" | "attention";
    detail: string;
  }[];
};

export function distillReadiness(
  snapshot: BookSnapshot,
  selection: ContentSelection | null = null,
  selectionFailed = false,
): DistillReadiness {
  const extractionComplete = snapshot.manifest.status === "done" && snapshot.manifest.n_pages > 0;
  const agentic = snapshot.agentic;
  const qualityReady =
    extractionComplete &&
    agentic?.status === "done" &&
    agentic.expected_page_count === snapshot.manifest.n_pages &&
    (agentic.quality_status === "complete" || agentic.quality_status === "complete_with_review");
  const qualityAttention =
    agentic?.status === "failed" ||
    (agentic?.status === "done" &&
      (agentic.expected_page_count !== snapshot.manifest.n_pages ||
        (agentic.quality_status !== "complete" && agentic.quality_status !== "complete_with_review")));
  const selectionReady =
    qualityReady &&
    selection !== null &&
    selection.agentic_run_id === agentic?.agentic_run_id &&
    selection.expected_page_count === snapshot.manifest.n_pages &&
    selection.included_page_count > 0;
  const selectionAttention = selectionFailed || (selection !== null && selection.included_page_count === 0);

  const steps: DistillReadiness["steps"] = [
    {
      id: "extraction",
      label: "Book extraction",
      status: extractionComplete
        ? "complete"
        : snapshot.manifest.status === "failed"
          ? "attention"
          : "waiting",
      detail: extractionComplete
        ? `${snapshot.manifest.n_pages} source pages are available.`
        : snapshot.manifest.status === "failed"
          ? "Extraction failed. Review and retry it in Ingestion."
          : "Complete book extraction in Ingestion first.",
    },
    {
      id: "quality",
      label: "Ingestion quality review",
      status: qualityReady ? "complete" : qualityAttention ? "attention" : "waiting",
      detail: qualityReady
        ? agentic?.quality_status === "complete_with_review"
          ? `${agentic.delivered_page_count} pages delivered; ${agentic.review_page_count} ${agentic.review_page_count === 1 ? "page is" : "pages are"} flagged for review.`
          : `${agentic?.delivered_page_count ?? 0} pages delivered and quality checks are complete.`
        : qualityAttention
          ? "The quality result needs attention or no longer matches this extraction."
          : extractionComplete
            ? "Complete the Agentic ingestion quality review in Ingestion."
            : "Available after book extraction is complete.",
    },
    {
      id: "selection",
      label: "Distill page selection",
      status: selectionReady ? "complete" : selectionAttention ? "attention" : "waiting",
      detail: selectionReady
        ? `${selection.included_page_count} pages included; ${selection.excluded_page_count} excluded in revision ${selection.revision}.`
        : selectionAttention
          ? "Review the page selection and include at least one page before starting Distill."
          : qualityReady
            ? "Load and review the Distill page selection before starting."
            : "Available after Ingestion quality review completes.",
    },
  ];
  const ready = steps.every((step) => step.status === "complete");

  return {
    ready,
    reason: ready
      ? "Source extraction and quality checks are ready for a Learning Guide."
      : "Complete the source checks below before creating a Learning Guide.",
    steps,
  };
}

export type DistillRunViewState = {
  runId: string;
  status: DistillStatus;
  progress: DistillRun["progress"];
  error: string | null;
};

export function initialDistillRunState(run: DistillRun | DistillRunStart): DistillRunViewState {
  return {
    runId: run.run_id,
    status: run.status,
    progress:
      "progress" in run
        ? run.progress
        : {
            current_node: null,
            node_status: {},
            completed_units: 0,
            total_units: 0,
            completed_chapters: 0,
            total_chapters: 0,
          },
    error: "error" in run ? run.error : null,
  };
}

export function reduceDistillRunState(state: DistillRunViewState, event: DistillEvent): DistillRunViewState {
  const status = statusForEvent(event.kind) ?? state.status;
  return {
    ...state,
    status,
    progress: event.progress ?? state.progress,
    error:
      event.kind === "run_failed"
        ? (event.error ?? "Distill could not complete.")
        : event.kind === "run_queued" || event.kind === "run_started"
          ? null
          : state.error,
  };
}

function statusForEvent(kind: string): DistillStatus | null {
  if (kind === "run_queued") return "queued";
  if (kind === "run_started") return "running";
  if (kind === "run_done") return "done";
  if (kind === "run_failed") return "failed";
  if (kind === "run_cancelled") return "cancelled";
  return null;
}
