import { describe, expect, it } from "vitest";

import { DEFAULT_STUDY_LAYOUT, parseStudyLayout, storeStudyLayout } from "./study-layout";

describe("study layout", () => {
  it("opens both side columns on the page and none in the reading window, with comfortable text", () => {
    expect(DEFAULT_STUDY_LAYOUT).toEqual({
      chapters: true,
      outline: true,
      readingChapters: false,
      readingOutline: false,
      width: "comfortable",
    });
  });

  it("falls back to the default for nothing stored, text that is not JSON, or JSON that is not an object", () => {
    expect(parseStudyLayout(null)).toEqual(DEFAULT_STUDY_LAYOUT);
    expect(parseStudyLayout("")).toEqual(DEFAULT_STUDY_LAYOUT);
    expect(parseStudyLayout("{oops")).toEqual(DEFAULT_STUDY_LAYOUT);
    expect(parseStudyLayout("42")).toEqual(DEFAULT_STUDY_LAYOUT);
    expect(parseStudyLayout("null")).toEqual(DEFAULT_STUDY_LAYOUT);
  });

  it("keeps the fields that are right and defaults only the ones that are wrong", () => {
    expect(parseStudyLayout('{"chapters":false,"outline":"yes","width":"wide","extra":1}')).toEqual({
      ...DEFAULT_STUDY_LAYOUT,
      chapters: false,
      width: "wide",
    });
  });

  it("applies a patch to the stored layout and round-trips", () => {
    const stored = storeStudyLayout(DEFAULT_STUDY_LAYOUT, { outline: false });
    expect(parseStudyLayout(stored)).toEqual({ ...DEFAULT_STUDY_LAYOUT, outline: false });
    const next = storeStudyLayout(parseStudyLayout(stored), { width: "wide", readingChapters: true });
    expect(parseStudyLayout(next)).toEqual({
      ...DEFAULT_STUDY_LAYOUT,
      outline: false,
      width: "wide",
      readingChapters: true,
    });
  });
});
