import { describe, expect, it } from "vitest";
import { agenticRun, snapshot } from "../../test-fixtures";
import { distillEventSchema, distillRunSchema } from "../entities";
import {
  distillReadiness,
  isDistillTerminalEvent,
  latestDistillRun,
  reduceDistillRunState,
  selectDistillRun,
} from "./run-state";

const run = (
  runId: string,
  createdAt: number,
  status: "queued" | "running" | "done" | "failed" | "cancelled" = "done",
) =>
  distillRunSchema.parse({
    run_id: runId,
    book_id: "book-a",
    extraction_run_id: "extract-a",
    ingestion_ai_run_id: "quality-a",
    status,
    progress: {},
    created_at: createdAt,
  });

describe("Distill run selection", () => {
  it("selects a requested run and falls back to the newest run", () => {
    const runs = [run("older", 1), run("newer", 2)];
    expect(selectDistillRun(runs, "older")?.run_id).toBe("older");
    expect(selectDistillRun(runs, "missing")?.run_id).toBe("newer");
    expect(latestDistillRun([])).toBeNull();
  });

  it("does not mutate the server list while selecting the latest run", () => {
    const runs = [run("older", 1), run("newer", 2)];
    latestDistillRun(runs);
    expect(runs.map((item) => item.run_id)).toEqual(["older", "newer"]);
  });
});

describe("Distill event state", () => {
  it("applies status and progress while preserving prior values absent from an event", () => {
    const first = run("run-a", 1, "queued");
    const state = {
      runId: first.run_id,
      status: first.status,
      progress: first.progress,
      error: null,
    };
    const next = reduceDistillRunState(
      state,
      distillEventSchema.parse({
        kind: "run_started",
        status: "running",
        progress: { current_node: "extract_unit" },
      }),
    );
    expect(next.status).toBe("running");
    expect(next.progress.current_node).toBe("extract_unit");
    expect(
      reduceDistillRunState(next, distillEventSchema.parse({ kind: "unit", status: "done" })).status,
    ).toBe("running");
  });

  it("marks terminal events and keeps failure text readable", () => {
    const state = {
      runId: "run-a",
      status: "running" as const,
      progress: run("run-a", 1, "running").progress,
      error: null,
    };
    const failed = distillEventSchema.parse({ kind: "run_failed", status: "failed" });
    expect(isDistillTerminalEvent(failed)).toBe(true);
    expect(reduceDistillRunState(state, failed).error).toBe("Distill could not complete.");
  });
});

describe("book readiness", () => {
  it("requires completed extraction and a publishable ingestion quality result", () => {
    expect(
      distillReadiness(snapshot({ status: "done" }, null, agenticRun({ expected_page_count: 9 }))).ready,
    ).toBe(true);
    expect(distillReadiness(snapshot({ status: "extracting" }, null, agenticRun())).ready).toBe(false);
    expect(
      distillReadiness(snapshot({ status: "done" }, null, agenticRun({ quality_status: null }))).ready,
    ).toBe(false);
  });
});
