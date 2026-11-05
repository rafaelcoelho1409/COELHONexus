import { describe, expect, it } from "vitest";

import { runStatusSchema } from "../entities";
import {
  ingestionNodeCaption,
  ingestionNodeStatus,
  ingestionNodeText,
  ingestionPercent,
  pageProgress,
  pageProgressSummary,
  phaseStatusText,
} from "./ingestion-progress";

function agenticStage(overrides: Record<string, unknown> = {}) {
  const status = runStatusSchema.parse({
    progress: {
      status: "running",
      stages: {
        agentic: {
          status: "running",
          current: 4,
          total: 40,
          current_node: "normalize_pages",
          node_status: { audit_pages: "done", normalize_pages: "running" },
          node_progress: {
            audit_pages: { current: 40, total: 40 },
            normalize_pages: { current: 4, total: 40 },
          },
          ...overrides,
        },
      },
    },
  });
  return status.progress?.stages?.agentic ?? null;
}

describe("phaseStatusText", () => {
  it("names the phase's own status", () => {
    expect(phaseStatusText("running", "running")).toBe("Processing");
    expect(phaseStatusText("done", "running")).toBe("Complete");
    expect(phaseStatusText("waiting", "running")).toBe("Waiting");
  });

  it("says what a run's end means for a phase that never ran", () => {
    expect(phaseStatusText(undefined, "failed")).toBe("Skipped");
    expect(phaseStatusText(null, "cancelled")).toBe("Stopped");
    expect(phaseStatusText(null, null)).toBe("Waiting");
  });
});

describe("ingestionPercent", () => {
  it("is null without a total and clamps to 0-100", () => {
    expect(ingestionPercent(3, 0)).toBeNull();
    expect(ingestionPercent(1, 3)).toBe(33);
    expect(ingestionPercent(9, 3)).toBe(100);
  });
});

describe("ingestionNodeStatus", () => {
  it("reads the node's own status", () => {
    const stage = agenticStage();
    expect(ingestionNodeStatus(stage, "audit_pages", "running")).toBe("done");
    expect(ingestionNodeStatus(stage, "normalize_pages", "running")).toBe("running");
    expect(ingestionNodeStatus(stage, "publish", "running")).toBe("pending");
  });

  it("does not leave a node running after the run ended", () => {
    const stage = agenticStage();
    expect(ingestionNodeStatus(stage, "normalize_pages", "failed")).toBe("failed");
    expect(ingestionNodeStatus(stage, "normalize_pages", "cancelled")).toBe("stopped");
  });

  it("marks the node a failed run was on", () => {
    const stage = agenticStage({ node_status: {}, current_node: "audit_pages" });
    expect(ingestionNodeStatus(stage, "audit_pages", "failed")).toBe("failed");
    expect(ingestionNodeStatus(null, "audit_pages", null)).toBe("pending");
  });

  it("has a label for each state", () => {
    expect(ingestionNodeText("pending")).toBe("Waiting");
    expect(ingestionNodeText("running")).toBe("In progress");
    expect(ingestionNodeText("done")).toBe("Complete");
    expect(ingestionNodeText("failed")).toBe("Failed");
    expect(ingestionNodeText("stopped")).toBe("Stopped");
  });
});

describe("ingestionNodeCaption", () => {
  it("counts pages for the page-by-page nodes only", () => {
    const stage = agenticStage();
    expect(ingestionNodeCaption(stage, "audit_pages")).toBe("40 of 40 pages audited");
    expect(ingestionNodeCaption(stage, "normalize_pages")).toBe("4 of 40 pages normalized");
    expect(ingestionNodeCaption(stage, "publish")).toBeNull();
  });

  it("says nothing before the total is known", () => {
    expect(ingestionNodeCaption(null, "audit_pages")).toBeNull();
    expect(ingestionNodeCaption(agenticStage({ node_progress: {} }), "audit_pages")).toBeNull();
  });
});

describe("pageProgress", () => {
  it("lists the page-by-page nodes in graph order and counts the finished ones", () => {
    const rows = pageProgress(agenticStage());
    expect(rows).toEqual([
      { id: "audit_pages", label: "Audit pages", processed: 40, total: 40 },
      { id: "normalize_pages", label: "Normalize pages", processed: 4, total: 40 },
    ]);
    expect(pageProgressSummary(rows)).toEqual({ completed: 1, total: 2 });
  });

  it("is empty until Agentic AI reports a total", () => {
    expect(pageProgress(null)).toEqual([]);
    expect(pageProgressSummary([])).toBeNull();
  });
});
