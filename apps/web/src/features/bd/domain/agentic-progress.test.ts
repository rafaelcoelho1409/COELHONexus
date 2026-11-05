import { describe, expect, it } from "vitest";

import type { AgenticProgress } from "../entities";
import {
  AGENTIC_NODE_DETAILS,
  AGENTIC_NODES,
  agenticBoardCaption,
  agenticNodeCaption,
  agenticNodeKpi,
  agenticNodeLiveLine,
  agenticNodeStatus,
  agenticNodeViews,
  agenticPercent,
  chapterProgress,
  chapterProgressSummary,
} from "./agentic-progress";

const progress: AgenticProgress = {
  current_node: "repair_pages",
  node_status: { audit_batch: "done", repair_pages: "running" },
  audited_pages: [1, 2, 3],
  audit_warning_pages: [],
  processed_pages: [1, 2],
  active_audit_pages: [],
  active_repair_pages: [3],
};

describe("agentic progress display", () => {
  it("calculates a bounded progress percentage", () => {
    expect(agenticPercent(3, 5)).toBe(60);
    expect(agenticPercent(9, 5)).toBe(100);
    expect(agenticPercent(1, 0)).toBeNull();
  });

  it("uses persisted node state and identifies failures", () => {
    expect(agenticNodeStatus(progress, "repair_pages", "running")).toBe("running");
    expect(agenticNodeStatus(progress, "normalize_metadata", "failed")).toBe("pending");
    expect(
      agenticNodeStatus({ ...progress, current_node: "normalize_metadata" }, "normalize_metadata", "failed"),
    ).toBe("failed");
  });

  it("summarizes audited and repaired pages, including concurrent work", () => {
    expect(agenticNodeCaption(progress, "audit_batch", 10)).toBe("3 of 10 pages audited");
    expect(agenticNodeCaption(progress, "repair_pages", 10)).toBe("2 pages repaired · 1 active · 10 total");
    expect(agenticNodeCaption(progress, "repair_pages", 10, "sections")).toBe(
      "2 sections repaired · 1 active · 10 total",
    );
    expect(agenticNodeCaption(progress, "publish", 10)).toBeNull();
  });

  it("gives a finished node a headline number and a running node a live line, never both", () => {
    const summary = { deliveredPages: 8, reviewPages: 2, unverifiedPages: 1 };
    expect(agenticNodeKpi(progress, "audit_batch", "done", "pages", summary)).toBe("3 pages audited");
    expect(agenticNodeKpi(progress, "repair_pages", "done", "sections", summary)).toBe("2 sections repaired");
    expect(agenticNodeKpi(progress, "publish", "done", "pages", summary)).toBe("8 delivered · 2 need review");
    expect(agenticNodeKpi(progress, "publish", "done", "pages", null)).toBeNull();
    expect(agenticNodeKpi(progress, "plan_repairs", "done", "pages", summary)).toBeNull();
    expect(agenticNodeKpi(progress, "audit_batch", "running", "pages", summary)).toBeNull();

    expect(agenticNodeLiveLine(progress, "repair_pages", "running", 10)).toBe("2/10 · 1 in flight");
    expect(agenticNodeLiveLine({ ...progress, active_repair_pages: [] }, "repair_pages", "running", 10)).toBe(
      "2/10",
    );
    expect(agenticNodeLiveLine(progress, "audit_batch", "done", 10)).toBeNull();
    expect(agenticNodeLiveLine(progress, "publish", "running", 10)).toBeNull();
    expect(agenticNodeLiveLine(progress, "repair_pages", "running", 0)).toBeNull();
  });

  it("builds the node views in run order and the caption above the graph", () => {
    const views = agenticNodeViews(progress, "running", 10, "pages", null);
    expect(views.map((view) => view.id)).toEqual(AGENTIC_NODES.map((node) => node.id));
    expect(views.map((view) => view.status)).toEqual(["done", "pending", "running", "pending", "pending"]);
    expect(views[0]?.kpi).toBe("3 pages audited");
    expect(views[2]?.liveLine).toBe("2/10 · 1 in flight");
    expect(agenticBoardCaption(progress, "running", true, 2, 10, "pages")).toBe(
      "Run running · 2 of 10 pages processed · 1 of 5 nodes done",
    );
    expect(agenticBoardCaption(progress, "pending", false, 0, 0, "pages")).toBe(
      "Ready to run after extraction · 1 of 5 nodes done",
    );
  });

  it("describes every node, with something to read and something it writes", () => {
    for (const node of AGENTIC_NODES) {
      const details = AGENTIC_NODE_DETAILS[node.id];
      expect(details.title).toBe(node.label);
      expect(details.actions.length).toBeGreaterThan(0);
      expect(details.reads.length).toBeGreaterThan(0);
      expect(details.writes.length).toBeGreaterThan(0);
    }
  });

  it("derives chapter completion from the page ranges", () => {
    const chapters = [
      { title: "Opening", first_page: 1, last_page: 2 },
      { title: "Later", first_page: 3, last_page: 5 },
    ];
    expect(chapterProgress(chapters, [1, 2, 3])).toEqual([
      { ...chapters[0], processed: 2, total: 2 },
      { ...chapters[1], processed: 1, total: 3 },
    ]);
    expect(chapterProgressSummary(chapters, [1, 2, 3])).toEqual({ completed: 1, total: 2 });
    expect(chapterProgressSummary([], [])).toBeNull();
  });
});
