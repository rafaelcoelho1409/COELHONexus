import { describe, expect, it } from "vitest";

import type { StageEvent } from "../entities";
import { activityLines, activityTone, MAX_ACTIVITY_LINES } from "./activity";

const at = (seconds: number) => 1_700_000_000 + seconds;

describe("activity tone", () => {
  it("marks failures and warnings, and nothing else", () => {
    expect(activityTone({ kind: "error" })).toBe("error");
    expect(activityTone({ kind: "llm_progress", error: "boom" })).toBe("error");
    expect(activityTone({ kind: "warning" })).toBe("warn");
    expect(activityTone({ kind: "warn" })).toBe("warn");
    expect(activityTone({ kind: "done" })).toBe("info");
    expect(activityTone({})).toBe("info");
  });
});

describe("activity lines", () => {
  const events: StageEvent[] = [
    { step: "off_topic", kind: "start", files: 12, ts: at(0) },
    { step: "off_topic", kind: "error", error: "judge timed out", ts: at(5) },
    { step: "off_topic", kind: "done", kept: 10, total: 12, wall_ms: 3200, ts: at(9) },
  ];

  it("words each event as the node does and numbers the lines in order", () => {
    const lines = activityLines("planner", events, 0);
    expect(lines.map((line) => [line.id, line.text.split(" — ")[0]])).toEqual([
      [0, "· filtering 12 files…"],
      [1, "error"],
      [2, "✓ kept 10/12 (3200 ms)"],
    ]);
    expect(lines.every((line) => line.time !== "")).toBe(true);
  });

  it("adds the reason to a failure, trimmed and bounded, but not to an ordinary event", () => {
    const [start, failure] = activityLines("planner", events, 0);
    expect(failure?.text).toBe("error — judge timed out");
    expect(failure?.tone).toBe("error");
    expect(start?.tone).toBe("info");

    const long = activityLines(
      "planner",
      [{ step: "off_topic", kind: "error", message: "x".repeat(500) }],
      0,
    );
    expect(long[0]?.text.length).toBeLessThanOrEqual("error — ".length + 160);

    const withNote = activityLines(
      "planner",
      [{ step: "off_topic", kind: "start", files: 1, reason: "r" }],
      0,
    );
    expect(withNote[0]?.text).toBe("· filtering 1 files…");
  });

  it("flags only the lines that arrived after the reader last had the node open", () => {
    const flags = (since: number) => activityLines("planner", events, since).map((line) => line.isNew);
    expect(flags(0)).toEqual([false, false, false]); // first visit: nothing to compare with
    expect(flags((at(9) + 1) * 1000)).toEqual([false, false, false]); // opened after the last event
    expect(flags(at(0) * 1000 + 1)).toEqual([false, true, true]);
    expect(activityLines("planner", [{ step: "off_topic", kind: "start" }], 1)[0]?.isNew).toBe(false); // no time on it
  });

  it("keeps the newest lines when there are more than the log holds", () => {
    const many: StageEvent[] = Array.from({ length: MAX_ACTIVITY_LINES + 25 }, (_, index) => ({
      step: "off_topic",
      kind: "start",
      files: index,
      ts: at(index),
    }));
    const lines = activityLines("planner", many, 0);
    expect(lines).toHaveLength(MAX_ACTIVITY_LINES);
    expect(lines[0]?.id).toBe(25); // ids stay the event's own position, so rows keep their keys
    expect(lines.at(-1)?.id).toBe(MAX_ACTIVITY_LINES + 24);
  });
});
