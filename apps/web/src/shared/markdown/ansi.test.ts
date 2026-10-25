import { describe, expect, it } from "vitest";

import { parseAnsi } from "./ansi";

const ESC = String.fromCharCode(27);

describe("parseAnsi", () => {
  it("returns plain text as one segment", () => {
    expect(parseAnsi("hello")).toEqual([{ text: "hello" }]);
    expect(parseAnsi("")).toEqual([]);
  });

  it("colours the text between a colour code and its reset", () => {
    expect(parseAnsi(`ok ${ESC}[32mPASS${ESC}[0m done`)).toEqual([
      { text: "ok " },
      { text: "PASS", tone: "green" },
      { text: " done" },
    ]);
  });

  it("combines bold with a colour and maps bright colours onto the same tones", () => {
    expect(parseAnsi(`${ESC}[1;31mFAIL${ESC}[0m ${ESC}[93mwarn${ESC}[0m`)).toEqual([
      { text: "FAIL", tone: "red", bold: true },
      { text: " " },
      { text: "warn", tone: "yellow" },
    ]);
  });

  it("treats black, white and grey as muted so they stay readable on both themes", () => {
    expect(parseAnsi(`${ESC}[30ma${ESC}[37mb${ESC}[90mc`)).toEqual([{ text: "abc", tone: "muted" }]);
  });

  it("drops cursor movement and other escapes, and keeps the text", () => {
    expect(parseAnsi(`${ESC}[2K${ESC}[1Gprogress ${ESC}[1A${ESC}]0;title\u0007done`)).toEqual([
      { text: "progress done" },
    ]);
  });

  it("a bare reset code clears the style", () => {
    expect(parseAnsi(`${ESC}[4munder${ESC}[mplain`)).toEqual([
      { text: "under", underline: true },
      { text: "plain" },
    ]);
  });
});
