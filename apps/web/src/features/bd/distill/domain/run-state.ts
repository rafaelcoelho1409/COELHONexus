import type { BookSnapshot } from "../../entities";
import type { DistillEvent, DistillRun, DistillRunStart } from "../entities";

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
};

export function distillReadiness(snapshot: BookSnapshot): DistillReadiness {
  if (snapshot.manifest.status !== "done") {
    return { ready: false, reason: "Finish book extraction before creating a Learning Guide." };
  }
  if (!snapshot.agentic) {
    return {
      ready: false,
      reason: "Complete Agentic ingestion quality review before creating a Learning Guide.",
    };
  }
  if (snapshot.agentic.status !== "done") {
    return {
      ready: false,
      reason: "Complete Agentic ingestion quality review before creating a Learning Guide.",
    };
  }
  if (snapshot.agentic.expected_page_count !== snapshot.manifest.n_pages) {
    return {
      ready: false,
      reason: "The ingestion quality result does not match the current book page count.",
    };
  }
  if (
    snapshot.agentic.quality_status !== "complete" &&
    snapshot.agentic.quality_status !== "complete_with_review"
  ) {
    return { ready: false, reason: "Agentic ingestion needs a publishable quality result first." };
  }
  return { ready: true, reason: "Extraction and ingestion quality checks are complete." };
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
