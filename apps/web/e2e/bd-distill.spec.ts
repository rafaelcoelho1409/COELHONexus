import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const BOOK = "9f8e7d6c5b4a3210";
const TITLE = "Designing Data-Intensive Applications: The Big Ideas Behind Reliable, Scalable Systems";
const FILE = "designing-data-intensive-applications-second-edition-final-proofs-2026-reprint.pdf";
const SOURCE = {
  source_type: "book",
  book_id: BOOK,
  extraction_run_id: "extract-1",
  ingestion_ai_run_id: "quality-1",
  chunk_id: "chunk-1",
  source_key: "pages/0001.md",
  page_number: 1,
  section_path: ["Foundations of Data Systems"],
  source_sha256: "a".repeat(64),
  quote: "Reliable systems keep working when parts fail.",
};
const LESSON = {
  title: "Design for failure",
  explanation: "A system should continue to serve useful work through partial failures.",
  source_refs: [SOURCE],
  attribution: "book",
  verification_status: "verified",
  confidence: 0.94,
};
const REPORT = {
  schema_version: "1.0",
  title: "Learning Guide: Designing Reliable Systems",
  book_id: BOOK,
  extraction_run_id: "extract-1",
  ingestion_ai_run_id: "quality-1",
  distill_run_id: "distill-1",
  generated_at: 1_793_000_000,
  report_scope: "book_only",
  central_thesis: "Reliable systems are designed to expect failure and recover safely.",
  central_thesis_attribution: "book",
  central_thesis_source_refs: [SOURCE],
  key_takeaways: [LESSON],
  cross_chapter_connections: [],
  chapters: [
    {
      title: "Foundations of Data Systems",
      overview: "Reliability and scalability shape useful data systems.",
      overview_attribution: "book",
      overview_source_refs: [SOURCE],
      lessons: [LESSON],
      open_questions: [],
      diagrams: [],
      equations: [],
      charts: [],
    },
  ],
  glossary: [
    {
      term: "Reliability",
      definition: "Continuing to work correctly when things go wrong.",
      attribution: "book",
      source_refs: [SOURCE],
      verification_status: "verified",
    },
  ],
  limitation_notes: [],
  review_notes: [],
  diagrams: [
    {
      title: "Failure recovery",
      accessible_description: "A service detects a failure and recovers before serving a response.",
      nodes: [
        {
          node_id: "detect",
          label: "Detect failure",
          attribution: "book",
          learning_unit_ids: ["unit-1"],
          evidence: [{ page_number: 1, quote: SOURCE.quote }],
        },
        {
          node_id: "recover",
          label: "Recover",
          attribution: "book",
          learning_unit_ids: ["unit-1"],
          evidence: [{ page_number: 1, quote: SOURCE.quote }],
        },
      ],
      edges: [
        {
          source_id: "detect",
          target_id: "recover",
          relation: "sequence",
          attribution: "book",
          learning_unit_ids: ["unit-1"],
          evidence: [{ page_number: 1, quote: SOURCE.quote }],
        },
      ],
    },
  ],
  equations: [],
  charts: [
    {
      title: "Observed latency",
      chart_type: "table",
      x_field: "Percentile",
      y_fields: ["Latency"],
      caption: "Latency by percentile.",
      accessible_description: "A table compares latency for two percentiles.",
      learning_unit_ids: ["unit-1"],
      dataset: {
        dataset_id: "latency-1",
        title: "Latency",
        columns: [
          { name: "Percentile", value_type: "text" },
          { name: "Latency", value_type: "number", unit: "ms" },
        ],
        rows: [
          {
            values: { Percentile: "p50", Latency: 12 },
            learning_unit_ids: ["unit-1"],
            evidence: [{ page_number: 1, quote: SOURCE.quote }],
          },
          {
            values: { Percentile: "p99", Latency: 480 },
            learning_unit_ids: ["unit-1"],
            evidence: [{ page_number: 1, quote: SOURCE.quote }],
          },
        ],
        transformations: [],
        assumptions: [],
        learning_unit_ids: ["unit-1"],
      },
    },
  ],
  covered_pages: [1],
  uncovered_pages: [],
  review_required: false,
};

const pageEntry = {
  page: 1,
  key: "pages/0001.md",
  chars: 2100,
  headings: 1,
  tables: 0,
  pictures: 0,
  formulas: 0,
  figures: [],
  section_path: ["Foundations of Data Systems"],
};

function bookSnapshot() {
  return {
    manifest: {
      book_id: BOOK,
      title: TITLE,
      filename: FILE,
      size_bytes: 18_874_368,
      n_pages: 9,
      status: "done",
      error: null,
      outline: [{ level: 1, title: "Foundations of Data Systems", page: 1 }],
      chapters: [{ title: "Foundations of Data Systems", first_page: 1, last_page: 9 }],
      pages: [pageEntry],
      created_at: 1_793_000_000,
      updated_at: 1_793_000_100,
    },
    progress: null,
    agentic: {
      book_id: BOOK,
      extraction_run_id: "extract-1",
      agentic_run_id: "quality-1",
      status: "done",
      expected_page_count: 9,
      published_page_count: 9,
      quality_status: "complete",
      delivered_page_count: 9,
      discarded_blank_page_count: 0,
      review_page_count: 0,
      review_pages: [],
      unverified_page_count: 0,
      unverified_pages: [],
      discarded_pages: [],
      image_only_pages: [],
      progress: {
        current_node: null,
        node_status: {},
        audited_pages: [],
        audit_warning_pages: [],
        processed_pages: [],
        active_audit_pages: [],
        active_repair_pages: [],
      },
      created_at: 1_793_000_000,
      updated_at: 1_793_000_100,
      finished_at: 1_793_000_100,
      error: null,
    },
  };
}

const run = (status: "done" | "running" | "failed" = "done") => ({
  schema_version: "1.0",
  workflow_version: "1.0",
  prompt_version: "1.0",
  renderer_version: "1.0",
  run_id: "distill-1",
  book_id: BOOK,
  extraction_run_id: "extract-1",
  ingestion_ai_run_id: "quality-1",
  expected_page_count: 9,
  status,
  progress: {
    current_node: status === "running" ? "synthesize_chapter" : null,
    node_status: { prepare_units: "done", extract_unit: status === "running" ? "running" : "done" },
    completed_units: 3,
    total_units: 8,
    completed_chapters: 0,
    total_chapters: 2,
  },
  structural_quality:
    status === "done"
      ? {
          expected_page_count: 9,
          substantive_page_count: 9,
          covered_substantive_pages: 9,
          page_coverage_rate: 1,
          source_reference_count: 3,
          valid_source_reference_count: 3,
          evidence_quote_count: 3,
          supported_evidence_quote_count: 3,
          unverified_lesson_count: 0,
          uncovered_substantive_pages: [],
          citation_integrity_passed: true,
          quote_support_passed: true,
          errors: [],
        }
      : null,
  covered_page_count: 9,
  uncovered_pages: [],
  review_required: false,
  created_at: 1_793_000_000,
  updated_at: 1_793_000_100,
  finished_at: status === "running" ? null : 1_793_000_100,
  error: status === "failed" ? "The sample run failed." : null,
});

async function mockBd(page: Page, selectedRun: "done" | "running" | "failed" | "none" = "done") {
  let starts = 0;
  await page.route("**/api/v1/bd/ingestion**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/bd/ingestion") {
      return route.fulfill({
        json: [
          {
            book_id: BOOK,
            title: TITLE,
            filename: FILE,
            size_bytes: 18_874_368,
            n_pages: 9,
            pages_done: 9,
            status: "done",
            created_at: 1_793_000_000,
            updated_at: 1_793_000_100,
          },
        ],
      });
    }
    if (path === `/api/v1/bd/ingestion/${BOOK}`) return route.fulfill({ json: bookSnapshot() });
    if (path === `/api/v1/bd/ingestion/${BOOK}/pages/1`) {
      return route.fulfill({
        json: {
          book_id: BOOK,
          page: 1,
          markdown: "# Foundations of Data Systems\n\nReliable systems keep working when parts fail.",
          content_source: "agentic",
        },
      });
    }
    return route.fulfill({ status: 404, body: "" });
  });
  await page.route("**/api/v1/bd/distill/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === `/api/v1/bd/distill/${BOOK}/runs` && request.method() === "GET") {
      return route.fulfill({
        json: selectedRun === "none" ? [] : [run(selectedRun)],
      });
    }
    if (path.endsWith("/report") && request.method() === "GET") return route.fulfill({ json: REPORT });
    if (path.endsWith("/events")) {
      return route.fulfill({
        headers: { "content-type": "text/event-stream" },
        body: 'data: {"kind":"run_started","status":"running"}\n\n',
      });
    }
    if (request.method() === "POST") starts += 1;
    return route.fulfill({ status: 404, body: "" });
  });
  return { starts: () => starts };
}

test.describe("Book Distiller · Distill", () => {
  test("shows the source-linked Learning Guide and leaves run controls gated", async ({ page, api }) => {
    await api("healthy");
    const state = await mockBd(page);
    await page.goto(`/bd/distill?book=${BOOK}`);

    await expect(page.getByRole("heading", { level: 2, name: TITLE })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Learning Guide: Designing Reliable Systems" }),
    ).toBeVisible();
    await expect(page.getByText("Page coverage")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Failure recovery" })).toBeVisible();
    await expect(page.getByText("A table compares latency for two percentiles.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Create another guide" })).toBeDisabled();
    expect(state.starts()).toBe(0);

    await page.getByRole("link", { name: "Page 1" }).first().click();
    await expect(page).toHaveURL(new RegExp(`/bd/ingestion\\?book=${BOOK}.*page=1`));
    await expect(page.getByRole("article", { name: "Page 1" })).toContainText(
      "Reliable systems keep working",
    );
  });

  test("shows readiness and empty states before a run exists", async ({ page, api }) => {
    await api("healthy");
    await mockBd(page, "none");
    await page.goto(`/bd/distill?book=${BOOK}`);
    await expect(page.getByText("No Learning Guide yet")).toBeVisible();
    await expect(
      page.getByText("Run controls are disabled while Distill is being structured."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Start Distill" })).toBeDisabled();
  });

  for (const scheme of ["dark", "light"] as const) {
    test(`finished guide has no WCAG 2.2 AA violations (${scheme})`, async ({ page, api }) => {
      await api("healthy");
      await mockBd(page);
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto(`/bd/distill?book=${BOOK}`);
      await expect(
        page.getByRole("heading", { name: "Learning Guide: Designing Reliable Systems" }),
      ).toBeVisible();
      const { violations } = await new AxeBuilder({ page }).withTags(WCAG).analyze();
      expect(
        violations.map(
          (violation) =>
            `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).join(" | ")}`,
        ),
      ).toEqual([]);
    });
  }

  test("does not scroll sideways or render text smaller than 12px", async ({ page, api }) => {
    await api("healthy");
    await mockBd(page);
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`/bd/distill?book=${BOOK}`);
    await expect(
      page.getByRole("heading", { name: "Learning Guide: Designing Reliable Systems" }),
    ).toBeVisible();
    const { overflow, small } = await page.evaluate(() => {
      const smallText = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
        const hasText = Array.from(element.childNodes).some(
          (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
        );
        return (
          hasText && !element.closest(".katex") && Number.parseFloat(getComputedStyle(element).fontSize) < 12
        );
      });
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        small: smallText.map((element) => `${element.tagName}.${element.className}`),
      };
    });
    expect(overflow).toBeLessThanOrEqual(0);
    expect(small).toEqual([]);
  });
});
