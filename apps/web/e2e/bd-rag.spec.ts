import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const BOOK = "book-1";
const TITLE = "Designing Data-Intensive Applications";

const row = {
  book_id: BOOK,
  title: TITLE,
  filename: "designing-data-intensive-applications.pdf",
  size_bytes: 18_874_368,
  n_pages: 620,
  pages_done: 620,
  status: "done",
  created_at: 1_793_000_000,
  updated_at: 1_793_000_100,
};

async function assertPageQuality(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
  expect(
    results.violations,
    JSON.stringify(
      results.violations.map((violation) => [violation.id, violation.nodes.map((n) => n.target)]),
    ),
  ).toEqual([]);
  const measurements = await page.evaluate(() => {
    const small = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
      if (element.closest(".katex")) return false;
      const hasText = Array.from(element.childNodes).some(
        (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
      );
      return hasText && Number.parseFloat(getComputedStyle(element).fontSize) < 12;
    });
    return {
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      small: small.map((element) => `${element.tagName}.${element.className}`),
    };
  });
  expect(measurements.overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
  expect(measurements.small, "text under 12px").toEqual([]);
}

async function mockLibrary(page: Page) {
  await page.route("**/api/v1/bd/ingestion", (route) => route.fulfill({ json: [row] }));
}

test.describe("Book Distiller · Agentic RAG", () => {
  test("asks one book and links the cited page back to the reader", async ({ page }) => {
    await mockLibrary(page);
    await page.route("**/api/v1/bd/agents/history/*", (route) => route.fulfill({ json: { items: [] } }));
    await page.route("**/api/v1/bd/agents/threads**", (route) => route.fulfill({ json: { items: [] } }));
    await page.route("**/api/v1/bd/agents/search/stream", (route) =>
      route.fulfill({
        contentType: "text/event-stream",
        body: [
          { node: "_meta", turn_id: 7 },
          { node: "retrieve", mode: "standard", documents: [], document_count: 1 },
          {
            node: "generate",
            generation: "Consensus requires a quorum [Page: 42].",
            citations: [
              {
                title: "Consistency and consensus",
                book_id: BOOK,
                page: 42,
                chapter: "Distributed Data",
                snippet: "A quorum requires agreement from enough replicas.",
                source: "qdrant_hybrid",
              },
            ],
          },
          { node: "end", duration_ms: 1200 },
        ]
          .map((event) => `data: ${JSON.stringify(event)}\n\n`)
          .join(""),
      }),
    );

    await page.goto(`/bd/ask?book=${BOOK}`);
    const box = page.getByRole("textbox", { name: "Ask about this book" });
    await expect(box).toBeVisible();
    await box.fill("What does the book say about consensus?");
    await box.press("Enter");
    await expect(page.getByText("Answered in 1.2s")).toBeVisible();
    if (await page.getByRole("complementary", { name: "Sources" }).isVisible()) {
      await expect(page.getByRole("link", { name: "Consistency and consensus" })).toHaveAttribute(
        "href",
        `/bd/pages?book=${BOOK}&page=42`,
      );
    } else {
      await page.getByRole("button", { name: "Sources (1)" }).click();
      await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
    }
    await assertPageQuality(page);
  });

  test("runs a BD data query and renders the result table", async ({ page }) => {
    await page.route("**/api/v1/bd/query/namespaces", (route) =>
      route.fulfill({
        json: {
          matrix: {
            bd: {
              elasticsearch: { available: true, label: "BD · pages and passages" },
              qdrant: { available: true, label: "BD · learning units" },
              neo4j: { available: true, label: "BD · books and concepts" },
            },
          },
        },
      }),
    );
    await page.route("**/api/v1/bd/query/raw/elasticsearch", (route) =>
      route.fulfill({
        json: {
          backend: "elasticsearch",
          app: "bd",
          ok: true,
          notes: [],
          took_ms: 9,
          total: 1,
          hits: [
            {
              summary: "Consistency and consensus",
              raw: {
                _index: "bd_pages",
                _id: `${BOOK}:42`,
                _score: 3.2,
                _source: { book_id: BOOK, page: 42, chapter: "Distributed Data" },
              },
            },
          ],
        },
      }),
    );
    await page.route("**/api/v1/bd/query/history**", (route) => route.fulfill({ json: { items: [] } }));

    await page.goto("/bd/query");
    await page.getByRole("button", { name: /^Run/ }).click();
    await expect(page.getByText("Done · 9 ms")).toBeVisible();
    await expect(page.getByRole("table")).toContainText("Distributed Data");
    await assertPageQuality(page);
  });
});
