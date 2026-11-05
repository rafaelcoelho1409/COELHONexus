import { describe, expect, it } from "vitest";

import {
  DEFAULT_READER_LAYOUT,
  parseReaderLayout,
  parseRememberedPage,
  storeReaderLayout,
} from "./reader-layout";

describe("reader layout", () => {
  it("opens both side columns on the page and none in the reading window, with comfortable text", () => {
    expect(DEFAULT_READER_LAYOUT).toEqual({
      contents: true,
      outline: true,
      readingContents: false,
      readingOutline: false,
      width: "comfortable",
    });
  });

  it("falls back to the default for nothing stored, text that is not JSON, or JSON that is not an object", () => {
    expect(parseReaderLayout(null)).toEqual(DEFAULT_READER_LAYOUT);
    expect(parseReaderLayout("")).toEqual(DEFAULT_READER_LAYOUT);
    expect(parseReaderLayout("{oops")).toEqual(DEFAULT_READER_LAYOUT);
    expect(parseReaderLayout("42")).toEqual(DEFAULT_READER_LAYOUT);
    expect(parseReaderLayout("null")).toEqual(DEFAULT_READER_LAYOUT);
  });

  it("keeps the fields that are right and defaults only the ones that are wrong", () => {
    expect(parseReaderLayout('{"contents":false,"outline":"yes","width":"wide","extra":1}')).toEqual({
      ...DEFAULT_READER_LAYOUT,
      contents: false,
      width: "wide",
    });
  });

  it("applies a patch to the stored layout and round-trips", () => {
    const stored = storeReaderLayout(DEFAULT_READER_LAYOUT, { outline: false });
    expect(parseReaderLayout(stored)).toEqual({ ...DEFAULT_READER_LAYOUT, outline: false });
    const next = storeReaderLayout(parseReaderLayout(stored), { width: "wide", readingContents: true });
    expect(parseReaderLayout(next)).toEqual({
      ...DEFAULT_READER_LAYOUT,
      outline: false,
      width: "wide",
      readingContents: true,
    });
  });
});

describe("remembered page", () => {
  it("returns the stored page only when the book has it", () => {
    expect(parseRememberedPage("42", 275)).toBe(42);
    expect(parseRememberedPage("275", 275)).toBe(275);
    expect(parseRememberedPage("276", 275)).toBeNull();
    expect(parseRememberedPage("0", 275)).toBeNull();
    expect(parseRememberedPage("3.5", 275)).toBeNull();
    expect(parseRememberedPage("abc", 275)).toBeNull();
    expect(parseRememberedPage(null, 275)).toBeNull();
    expect(parseRememberedPage("7", 0)).toBeNull();
  });
});
