import { describe, expect, it } from "vitest";

import type { PageQuality } from "../entities";
import { describeMechanicalFixes, dispositionNote, issueCounts, issueLabel } from "./page-quality";

const quality = (over: Partial<PageQuality> = {}): PageQuality => ({
  book_id: "book",
  agentic_run_id: "run",
  page: 3,
  page_kind: "text",
  status: "done",
  delivery_disposition: "include",
  issues: [],
  mechanical_fixes: {},
  audit_warnings: [],
  errors: [],
  ...over,
});

const issue = (resolution: PageQuality["issues"][number]["resolution"]): PageQuality["issues"][number] => ({
  issue_id: resolution,
  code: "split_word",
  severity: "medium",
  description: "A heading word is split.",
  evidence: ["L ANDSCAPE"],
  resolution,
  proposal: null,
});

describe("page quality words", () => {
  it("labels known codes and makes a readable label of an unknown one", () => {
    expect(issueLabel("toc_noise")).toBe("Contents formatting");
    expect(issueLabel("brand_new_code")).toBe("Brand new code");
  });

  it("describes what was removed automatically, with singular and plural nouns", () => {
    expect(describeMechanicalFixes({})).toBeNull();
    expect(describeMechanicalFixes({ control_characters: 0 })).toBeNull();
    expect(describeMechanicalFixes({ control_characters: 1 })).toBe(
      "Removed 1 control character automatically.",
    );
    expect(describeMechanicalFixes({ control_characters: 2, soft_hyphen: 1 })).toBe(
      "Removed 2 control characters and 1 soft hyphen automatically.",
    );
  });

  it("counts issues by what happened to them", () => {
    const counts = issueCounts(
      quality({ issues: [issue("review"), issue("review"), issue("auto_applied"), issue("none")] }),
    );
    expect(counts).toEqual({ review: 2, fixed: 1, noted: 1 });
  });

  it("explains why a blank or image-only page is not delivered text", () => {
    expect(dispositionNote(quality())).toBeNull();
    expect(dispositionNote(quality({ delivery_disposition: "discarded_blank" }))).toMatch(/Blank page/);
    expect(dispositionNote(quality({ delivery_disposition: "image_only" }))).toMatch(/Image-only/);
  });
});
