import { describe, expect, it } from "vitest";

import { distillEventSchema, distillRunListSchema, learningGuideSchema } from "./entities";

const sourceRef = {
  source_type: "book",
  book_id: "book-a",
  extraction_run_id: "extract-a",
  ingestion_ai_run_id: "quality-a",
  chunk_id: "chunk-a",
  source_key: "pages/0001.md",
  page_number: 1,
  section_path: ["Chapter 1"],
  source_sha256: "a".repeat(64),
  quote: "A short evidence excerpt.",
};

const lesson = {
  title: "A core principle",
  explanation: "A readable explanation.",
  source_refs: [sourceRef],
  attribution: "book",
  verification_status: "verified",
  confidence: 0.94,
};

const report = {
  schema_version: "1.0",
  title: "Learning Guide",
  book_id: "book-a",
  extraction_run_id: "extract-a",
  ingestion_ai_run_id: "quality-a",
  distill_run_id: "distill-a",
  generated_at: 1_793_000_000,
  report_scope: "book_only",
  central_thesis: "The book's central idea.",
  central_thesis_attribution: "book",
  central_thesis_source_refs: [sourceRef],
  key_takeaways: [lesson],
  cross_chapter_connections: [],
  chapters: [
    {
      title: "Chapter 1",
      overview: "A chapter summary.",
      overview_attribution: "book",
      overview_source_refs: [sourceRef],
      lessons: [lesson],
      open_questions: [],
      diagrams: [],
      equations: [],
      charts: [],
    },
  ],
  glossary: [],
  limitation_notes: [],
  review_notes: [],
  diagrams: [],
  equations: [],
  charts: [],
  covered_pages: [1],
  uncovered_pages: [],
  review_required: false,
};

describe("Distill response schemas", () => {
  it("parses a run list with additive manifest fields", () => {
    const result = distillRunListSchema.parse([
      {
        run_id: "distill-a",
        book_id: "book-a",
        extraction_run_id: "extract-a",
        ingestion_ai_run_id: "quality-a",
        status: "done",
        progress: {},
        created_at: 1,
        updated_at: 2,
        finished_at: 2,
        report_key: "report.json",
        future_field: "ignored",
      },
    ]);
    expect(result[0]?.run_id).toBe("distill-a");
  });

  it("parses run progress events and leaves unknown progress kinds readable", () => {
    expect(
      distillEventSchema.parse({
        kind: "unit",
        status: "done",
        unit_id: "unit-a",
        progress: { completed_units: 2 },
      }),
    ).toMatchObject({ kind: "unit", progress: { completed_units: 2 } });
    expect(distillEventSchema.safeParse({ status: "running" }).success).toBe(false);
  });

  it("requires source-linked report content and parses the typed Learning Guide", () => {
    const parsed = learningGuideSchema.parse(report);
    expect(parsed.chapters[0]?.lessons[0]?.source_refs[0]?.page_number).toBe(1);
    expect(learningGuideSchema.safeParse({ ...report, central_thesis_source_refs: [] }).success).toBe(false);
  });
});
