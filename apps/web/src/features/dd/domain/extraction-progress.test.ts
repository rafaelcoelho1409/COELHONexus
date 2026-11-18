import { describe, expect, it } from "vitest";

import { extractionPercent, phaseStatusText } from "./extraction-progress";

describe("phaseStatusText", () => {
  it("uses the phase status when it is known", () => {
    expect(phaseStatusText("running", "running")).toBe("Processing");
    expect(phaseStatusText("done", "running")).toBe("Complete");
    expect(phaseStatusText("waiting", "running")).toBe("Waiting");
  });

  it("describes a phase that did not run", () => {
    expect(phaseStatusText(undefined, "failed")).toBe("Skipped");
    expect(phaseStatusText(null, "cancelled")).toBe("Stopped");
    expect(phaseStatusText(null, null)).toBe("Waiting");
  });
});

describe("extractionPercent", () => {
  it("returns no percentage without a total and clamps to 0–100", () => {
    expect(extractionPercent(3, 0)).toBeNull();
    expect(extractionPercent(1, 3)).toBe(33);
    expect(extractionPercent(9, 3)).toBe(100);
  });
});
