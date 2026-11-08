import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ChapterCell } from "../domain/chapters";
import { ChapterStrip } from "./stage-parts";

describe("ChapterStrip", () => {
  it("keeps excluded source files and the reason visible and prevents running them", async () => {
    const chapter: ChapterCell = {
      id: "api-reference",
      title: "API Reference",
      status: "pending",
      wallMs: null,
      threadId: null,
      selected: false,
      sourceFiles: ["api.md", "reference.md"],
      synthEligible: false,
      exclusionReason: "Lookup-only API detail.",
    };
    render(
      <ChapterStrip
        cells={[chapter]}
        counter="0 / 0 · 1 excluded from Synth"
        runEnabled
        runTitle=""
        studyHref={(id) => `/study/${id}`}
        onRun={vi.fn()}
        onWipe={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByText("Excluded from Synth")).toBeInTheDocument();
    expect(screen.getByText("Lookup-only API detail.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run only API Reference" })).toBeDisabled();
    await userEvent.click(screen.getByText("2 source files"));
    expect(screen.getByText("api.md")).toBeInTheDocument();
    expect(screen.getByText("reference.md")).toBeInTheDocument();
  });
});
