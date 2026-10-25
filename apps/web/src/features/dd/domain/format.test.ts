import { describe, expect, it } from "vitest";

import { fmtAge, fmtBytes, fmtInt, fmtMs } from "./format";

describe("formatting", () => {
  it("formats bytes", () => {
    expect(fmtBytes(0)).toBe("0 B");
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(2048)).toBe("2.0 KB");
    expect(fmtBytes(1536 * 1024)).toBe("1.5 MB");
  });

  it("formats ages", () => {
    const now = Math.floor(Date.now() / 1000);
    expect(fmtAge(now - 30)).toBe("30s ago");
    expect(fmtAge(now - 3600)).toBe("1h ago");
    expect(fmtAge(now - 3 * 86400)).toBe("3d ago");
    expect(fmtAge(null)).toBe("");
  });

  it("formats run times", () => {
    expect(fmtMs(null)).toBe("0 ms");
    expect(fmtMs(450)).toBe("450 ms");
    expect(fmtMs(4100)).toBe("4.1 s");
    expect(fmtMs(125_000)).toBe("2m 5s");
  });

  it("formats whole numbers and treats anything else as zero", () => {
    expect(fmtInt(1234567)).toBe("1,234,567");
    expect(fmtInt("12")).toBe("0");
    expect(fmtInt(Number.NaN)).toBe("0");
    expect(fmtInt(undefined)).toBe("0");
  });
});
