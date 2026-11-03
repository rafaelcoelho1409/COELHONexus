import { describe, expect, it } from "vitest";

import { bdEventSchema } from "../entities";
import { pageEvent, sliceDone, startedEvent, terminalEvent } from "../test-fixtures";
import { EMPTY_RUN, effectiveStatus, pagesPerMinute, type RunState, runReducer } from "./run-state";

const parse = (raw: unknown) => bdEventSchema.parse(raw);
const apply = (state: RunState, ...raws: unknown[]) =>
  raws.reduce<RunState>((acc, raw) => runReducer(acc, { type: "event", event: parse(raw) }), state);

describe("runReducer", () => {
  it("builds the picture from the stream: title, outline, pages, counters", () => {
    const state = apply(EMPTY_RUN, startedEvent, pageEvent(1), pageEvent(2), sliceDone);
    expect(state.title).toMatch(/Designing Data-Intensive/);
    expect(state.nPages).toBe(9);
    expect(state.outline).toHaveLength(4);
    expect(state.chapters).toHaveLength(2);
    expect(Object.keys(state.pages)).toEqual(["1", "2"]);
    expect(state.newest).toBe(2);
    expect(state.completed).toBe(2);
    expect(state.total).toBe(9);
    expect(state.terminal).toBeNull();
  });

  it("gives the same picture when the server replays the history again (a reconnect)", () => {
    const events = [startedEvent, pageEvent(1), pageEvent(2), pageEvent(3), sliceDone];
    const once = apply(EMPTY_RUN, ...events);
    const twice = apply(once, ...events);
    expect(twice).toEqual(once);
  });

  it("keeps a page text, its figures and its section path", () => {
    const state = apply(EMPTY_RUN, pageEvent(2));
    expect(state.pages[2]).toMatchObject({
      figures: ["p0002-1.png"],
      sectionPath: ["Foundations of Data Systems", "Reliability"],
    });
    expect(state.pages[2]?.markdown).toContain("Reliability");
  });

  it("never moves the progress backwards", () => {
    const state = apply(EMPTY_RUN, pageEvent(5), pageEvent(2));
    expect(state.completed).toBe(5);
    expect(state.newest).toBe(5);
  });

  it("ends on a terminal event and forgets it when a new run starts", () => {
    const ended = apply(
      EMPTY_RUN,
      startedEvent,
      terminalEvent("failed", { error: "docling crashed on page 4" }),
    );
    expect(ended.terminal).toEqual({
      kind: "failed",
      pages: 3,
      total: 9,
      error: "docling crashed on page 4",
    });
    expect(runReducer(ended, { type: "restart" }).terminal).toBeNull();
    expect(apply(ended, startedEvent).terminal).toBeNull();
  });

  it("clears the cancelling state when the run ends, or after the safety time", () => {
    const cancelling = runReducer(EMPTY_RUN, { type: "canceling" });
    expect(cancelling.canceling).toBe(true);
    expect(apply(cancelling, terminalEvent("cancelled")).canceling).toBe(false);
    expect(runReducer(cancelling, { type: "cancel-timeout" }).canceling).toBe(false);
  });
});

describe("pagesPerMinute", () => {
  it("is null before the first slice", () => {
    expect(pagesPerMinute({})).toBeNull();
  });

  it("sums pages and seconds over the slices, counting each slice once", () => {
    const state = apply(
      EMPTY_RUN,
      sliceDone,
      sliceDone, // replayed
      { kind: "slice_done", ts: 10, first: 4, last: 5, seconds: 8 },
    );
    expect(pagesPerMinute(state.slices)).toBeCloseTo((5 / 20) * 60, 5);
  });
});

describe("effectiveStatus", () => {
  it("lets the stream's ending win over a stale snapshot", () => {
    expect(effectiveStatus("extracting", "running", { kind: "done", pages: 9, total: 9, error: null })).toBe(
      "done",
    );
  });

  it("shows a live run's progress, else the manifest", () => {
    expect(effectiveStatus("queued", "running", null)).toBe("extracting");
    expect(effectiveStatus("cancelled", null, null)).toBe("cancelled");
    expect(effectiveStatus("done", "done", null)).toBe("done");
  });
});
