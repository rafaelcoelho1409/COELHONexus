import { describe, expect, it } from "vitest";

import { DISTILL_NODES, distillBoardCaption, distillNodeStatus, distillNodeViews } from "./distill-progress";

const progress = {
  current_node: "synthesize_chapter",
  node_status: {
    prepare_units: "done",
    extract_unit: "done",
    prepare_chapters: "done",
    synthesize_chapter: "running",
  },
  completed_units: 12,
  total_units: 12,
  completed_chapters: 3,
  total_chapters: 8,
};

describe("Distill pipeline progress", () => {
  it("keeps the six Distill stages in workflow order before a run exists", () => {
    const views = distillNodeViews(null, null);
    expect(views.map((view) => view.id)).toEqual(DISTILL_NODES.map((node) => node.id));
    expect(views.every((view) => view.status === "pending")).toBe(true);
    expect(distillBoardCaption(null, null)).toBe("Select a stage to inspect its inputs and outputs.");
  });

  it("shows committed stages, the active stage, and its available progress", () => {
    const views = distillNodeViews("running", progress);
    expect(views.find((view) => view.id === "prepare_units")?.status).toBe("done");
    expect(views.find((view) => view.id === "synthesize_chapter")).toMatchObject({
      status: "running",
      liveLine: "3/8 chapters",
    });
    expect(distillBoardCaption("running", progress)).toBe("Working on explain chapters.");
  });

  it("does not treat unknown node statuses as completed", () => {
    const incomplete = {
      ...progress,
      current_node: null,
      node_status: { prepare_units: "unknown" },
    };
    expect(distillNodeStatus("prepare_units", "running", incomplete)).toBe("pending");
  });

  it("uses terminal status to distinguish complete, failed, and stopped runs", () => {
    expect(distillNodeStatus("publish_report", "done", null)).toBe("done");
    expect(distillNodeStatus("synthesize_chapter", "failed", { ...progress, node_status: {} })).toBe(
      "failed",
    );
    expect(distillNodeStatus("synthesize_chapter", "cancelled", progress)).toBe("pending");
    expect(distillBoardCaption("cancelled", progress)).toBe(
      "Run stopped before the Learning Guide was published.",
    );
  });
});
