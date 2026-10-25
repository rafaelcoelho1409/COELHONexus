import { describe, expect, it } from "vitest";

import type { StageEvent } from "../entities";
import {
  appendStageEvent,
  generateThreadId,
  PLANNER_NODES,
  plannerStartLabel,
  SYNTH_NODES,
  stageAllImplementedDone,
  stageNodeViews,
  stageProgressLabel,
  terminalPill,
} from "./stages";

const ALL_PLANNER_IDS = new Set(PLANNER_NODES.map((node) => node.id));

describe("stage node views", () => {
  it("marks committed nodes done, the first uncommitted one running, the rest pending", () => {
    const views = stageNodeViews(
      PLANNER_NODES,
      { raw_files: [], relevant_files: [] },
      [],
      true,
      ALL_PLANNER_IDS,
    );
    expect(views.slice(0, 4).map((view) => view.status)).toEqual(["done", "done", "running", "pending"]);
  });

  it("trusts the server's scheduled node over the guess, but only while running", () => {
    const running = stageNodeViews(PLANNER_NODES, { raw_files: [] }, ["plan_write"], true, ALL_PLANNER_IDS);
    expect(running.find((view) => view.status === "running")?.node.id).toBe("plan_write");

    const idle = stageNodeViews(PLANNER_NODES, { raw_files: [] }, ["plan_write"], false, ALL_PLANNER_IDS);
    expect(idle.some((view) => view.status === "running")).toBe(false);
  });

  it("shows a node the server does not implement as future, whatever its values say", () => {
    const views = stageNodeViews(PLANNER_NODES, { raw_files: [] }, [], false, new Set(["off_topic"]));
    expect(views[0]?.status).toBe("future");
    expect(views[1]?.status).toBe("pending");
  });

  it("reports progress as running position over implemented count, and nothing when idle", () => {
    const views = stageNodeViews(PLANNER_NODES, { raw_files: [] }, [], true, ALL_PLANNER_IDS);
    expect(stageProgressLabel(PLANNER_NODES, views, ALL_PLANNER_IDS)).toBe("2/8");
    const idle = stageNodeViews(PLANNER_NODES, {}, [], false, ALL_PLANNER_IDS);
    expect(stageProgressLabel(PLANNER_NODES, idle, ALL_PLANNER_IDS)).toBeNull();
  });

  it("is done only when every implemented node has committed", () => {
    const committed = Object.fromEntries(PLANNER_NODES.map((node) => [node.field, {}]));
    expect(stageAllImplementedDone(PLANNER_NODES, committed, ALL_PLANNER_IDS)).toBe(true);
    expect(stageAllImplementedDone(PLANNER_NODES, { raw_files: [] }, ALL_PLANNER_IDS)).toBe(false);
    expect(stageAllImplementedDone(PLANNER_NODES, { raw_files: [] }, new Set(["corpus_load"]))).toBe(true);
    expect(stageAllImplementedDone(PLANNER_NODES, committed, new Set())).toBe(false);
  });
});

describe("run helpers", () => {
  it("turns a terminal status into the pill", () => {
    expect(terminalPill("done")).toEqual({ status: "done", label: null });
    expect(terminalPill("failed")).toEqual({ status: "failed", label: "Failed" });
    expect(terminalPill("cancelled")).toEqual({ status: "cancelled", label: "Cancelled" });
  });

  it("starts as Resume once the plan's path is in the checkpoint", () => {
    expect(plannerStartLabel({})).toBe("Start");
    expect(plannerStartLabel({ raw_files: ["a"] })).toBe("Start");
    expect(plannerStartLabel({ plan_path: "planner/alpha/plan-latest.json" })).toBe("Resume");
  });

  it("keeps a bounded tail of events, oldest first", () => {
    const log = [1, 2, 3, 4].reduce<StageEvent[]>(
      (all, n) => appendStageEvent(all, { step: "s", kind: `k${n}` }, 3),
      [],
    );
    expect(log.map((event) => event.kind)).toEqual(["k2", "k3", "k4"]);
  });

  it("ignores a replayed event it already has", () => {
    const event: StageEvent = { step: "off_topic", kind: "start", ts: 100 };
    const log = appendStageEvent(appendStageEvent([], event, 10), { ...event }, 10);
    expect(log).toHaveLength(1);
    // Same step and kind at another time is a new event, and so is anything without a timestamp.
    expect(appendStageEvent(log, { ...event, ts: 101 }, 10)).toHaveLength(2);
    expect(appendStageEvent(appendStageEvent([], { step: "a" }, 10), { step: "a" }, 10)).toHaveLength(2);
  });

  it("names every node by the backend's real state keys", () => {
    // These come from the backend's PlannerState / SynthState; a typo here would leave a node 'pending' forever.
    expect(PLANNER_NODES.map((node) => node.field)).toEqual([
      "raw_files",
      "relevant_files",
      "doc_distill_ref",
      "chapter_proposals_ref",
      "chapter_doc_assignments_ref",
      "chapter_plan_ref",
      "chapter_order_ref",
      "plan_path",
    ]);
    expect(SYNTH_NODES.map((node) => node.field)).toEqual([
      "outline_path",
      "digest_path",
      "sawc_path",
      "derive_stats",
      "checklist_path",
      "mgsr_path",
      "chapter_path",
    ]);
  });

  it("builds docs-distiller/slug/uuid thread ids, which is how the server finds a framework's runs", () => {
    expect(generateThreadId("alpha")).toMatch(/^docs-distiller\/alpha\/[0-9a-f-]{36}$/);
  });

  it("falls back to its own uuid where randomUUID does not exist (plain http)", () => {
    const original = crypto.randomUUID;
    try {
      Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
      expect(generateThreadId("beta", () => 0)).toBe(
        "docs-distiller/beta/00000000-0000-4000-8000-000000000000",
      );
    } finally {
      Object.defineProperty(crypto, "randomUUID", { value: original, configurable: true });
    }
  });
});
