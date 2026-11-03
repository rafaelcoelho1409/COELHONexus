import { describe, expect, it } from "vitest";

import { bdEventSchema, isTerminalEvent, startResponseSchema } from "./entities";
import { pageEvent, startedEvent, terminalEvent } from "./test-fixtures";

describe("bdEventSchema", () => {
  it("reads every kind the server sends", () => {
    for (const raw of [startedEvent, pageEvent(1), { kind: "slice_done", first: 1, last: 5, seconds: 20 }]) {
      expect(bdEventSchema.safeParse(raw).success).toBe(true);
    }
    for (const kind of ["done", "failed", "cancelled"] as const) {
      expect(isTerminalEvent(bdEventSchema.parse(terminalEvent(kind)))).toBe(true);
    }
    expect(isTerminalEvent(bdEventSchema.parse(pageEvent(1)))).toBe(false);
  });

  it("rejects a kind it does not know, so the stream skips it", () => {
    expect(bdEventSchema.safeParse({ kind: "telepathy" }).success).toBe(false);
  });

  it("falls back on a missing optional field instead of failing the event", () => {
    const parsed = bdEventSchema.parse({ kind: "page", page: 4 });
    expect(parsed).toMatchObject({ page: 4, markdown: "", truncated: false, figures: [], section_path: [] });
    expect(bdEventSchema.parse({ kind: "failed" })).toMatchObject({ error: null, pages: 0, total: 0 });
  });

  it("needs the page number: a page event without one is not a page", () => {
    expect(bdEventSchema.safeParse({ kind: "page", markdown: "x" }).success).toBe(false);
  });
});

describe("startResponseSchema", () => {
  it("accepts the three answers and refuses others", () => {
    for (const status of ["queued", "cached", "locked"]) {
      expect(
        startResponseSchema.safeParse({ status, book_id: "b", run_id: null, resumed: false }).success,
      ).toBe(true);
    }
    expect(startResponseSchema.safeParse({ status: "maybe", book_id: "b" }).success).toBe(false);
  });
});
