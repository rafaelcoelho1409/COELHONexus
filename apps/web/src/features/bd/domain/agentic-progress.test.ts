import { describe, expect, it } from "vitest";

import type { AgenticProgress } from "../entities";
import {
  agenticNodeCaption,
  agenticNodeStatus,
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
