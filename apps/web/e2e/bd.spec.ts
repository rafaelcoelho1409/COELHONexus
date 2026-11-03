import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

// Real-looking data: a long title and file name, nested section paths, a table, a code fence that carries no
// language tag, a long figure name. The page must wrap all of it and never scroll sideways.
const BOOK = "9f8e7d6c5b4a3210";
const TITLE = "Designing Data-Intensive Applications: The Big Ideas Behind Reliable, Scalable Systems";
const FILE = "designing-data-intensive-applications-second-edition-final-proofs-2026-reprint.pdf";
const FIGURE = `p0002-${"very-long-figure-name-".repeat(4)}1.png`;
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

const OUTLINE = [
  { level: 1, title: "Foundations of Data Systems", page: 1 },
  { level: 2, title: "Reliability", page: 2 },
  { level: 2, title: "Scalability", page: 3 },
  { level: 1, title: "Distributed Data", page: 6 },
];
const CHAPTERS = [
  { title: "Foundations of Data Systems", first_page: 1, last_page: 5 },
  { title: "Distributed Data", first_page: 6, last_page: 9 },
];
const TEXT: Record<number, string> = {
  1: "# Foundations of Data Systems\n\nThe Internet was done so well that most people think of it as a natural resource.\n\n```\nGET /v1/orders?limit=20&cursor=eyJwYWdlIjoyfQ==\n```",
  2: "## Reliability\n\nContinuing to work correctly, even when things go wrong.",
  3: "## Scalability\n\n| Percentile | Latency |\n|---|---|\n| p50 | 12 ms |\n| p99 | 480 ms |",
};

const stats = (page: number) => ({
  chars: 2400,
  headings: page === 1 ? 1 : 0,
  tables: page === 3 ? 1 : 0,
  pictures: page === 2 ? 1 : 0,
  formulas: 0,
});
const path = (page: number) => [
  "Foundations of Data Systems",
  page === 1 ? "Chapter 1" : page === 2 ? "Reliability" : "Scalability",
];
const pageEvent = (page: number) => ({
  kind: "page",
  ts: page,
  page,
  markdown: TEXT[page] ?? `Page ${page}.`,
  truncated: false,
  stats: stats(page),
  figures: page === 2 ? [FIGURE] : [],
  section_path: path(page),
  completed: page,
  total: 9,
});
const entry = (page: number) => ({
  page,
  key: `pages/000${page}.md`,
  ...stats(page),
  figures: page === 2 ? [FIGURE] : [],
  section_path: path(page),
});
const STARTED = {
  kind: "started",
  ts: 0,
  title: TITLE,
  n_pages: 9,
  outline: OUTLINE,
  chapters: CHAPTERS,
  resumed: false,
  completed: 0,
};
const SLICE = { kind: "slice_done", ts: 4, first: 1, last: 3, seconds: 12 };
const END = (kind: "done" | "failed" | "cancelled", error: string | null = null) => ({
  kind,
  ts: 9,
  pages: kind === "done" ? 9 : 3,
  total: 9,
  error,
});
const PAGES = [pageEvent(1), pageEvent(2), pageEvent(3)];

const row = (status: string, done: number) => ({
  book_id: BOOK,
  title: TITLE,
  filename: FILE,
  size_bytes: 18_874_368,
  n_pages: 9,
  pages_done: done,
  status,
  created_at: 1_793_000_000,
  updated_at: 1_793_000_100,
});
const snapshot = (status: string, withPages: boolean, error: string | null = null) => ({
  manifest: {
    book_id: BOOK,
    title: TITLE,
    filename: FILE,
    size_bytes: 18_874_368,
    n_pages: 9,
    status,
    error,
    outline: OUTLINE,
    chapters: CHAPTERS,
    pages: withPages ? [entry(1), entry(2), entry(3)] : [],
    created_at: 1_793_000_000,
    updated_at: 1_793_000_100,
  },
  progress: ["queued", "extracting"].includes(status)
    ? {
        run_id: "run-1",
        status: status === "queued" ? "queued" : "running",
        completed: withPages ? 3 : 0,
        total: 9,
        error: null,
        started_at: 1_793_000_000,
        updated_at: 1_793_000_100,
      }
    : null,
});

const sse = (events: unknown[]) => `${events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("")}\n\n`;

type State = {
  status: string;
  withPages: boolean;
  error?: string | null;
  inLibrary: boolean;
  /** What the event stream sends for the current state. */
  events: () => unknown[];
  calls: string[];
  onCall?: (call: string) => void;
};

async function mockBd(page: Page, state: State) {
  const answer = (call: string) => {
    state.calls.push(call);
    state.onCall?.(call);
  };
  await page.route("**/api/v1/bd/ingestion**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const tail = url.pathname.replace("/api/v1/bd/ingestion", "");
    const method = request.method();
    const json = (body: unknown) => route.fulfill({ json: body });
    if (tail === "" && method === "GET") {
      return json(state.inLibrary ? [row(state.status, state.withPages ? 3 : 0)] : []);
    }
    if (tail === "" && method === "POST") {
      answer("upload");
      state.inLibrary = true;
      return json({ status: "queued", book_id: BOOK, run_id: "run-1", resumed: false });
    }
    if (tail === `/${BOOK}` && method === "GET") {
      return json(snapshot(state.status, state.withPages, state.error ?? null));
    }
    if (tail === `/${BOOK}/events`) {
      return route.fulfill({
        headers: { "content-type": "text/event-stream" },
        body: sse(state.events()),
      });
    }
    if (tail === `/${BOOK}/cancel` && method === "POST") {
      answer("cancel");
      return json({ book_id: BOOK, status: "cancelling" });
    }
    if (tail === `/${BOOK}/run` && method === "POST") {
      answer(url.searchParams.get("refresh") === "true" ? "run-refresh" : "run");
      return json({ status: "queued", book_id: BOOK, run_id: "run-2", resumed: true });
    }
    if (tail.startsWith(`/${BOOK}/pages/`)) {
      const n = Number(tail.split("/").pop());
      return json({ book_id: BOOK, page: n, markdown: TEXT[n] ?? `Page ${n}.` });
    }
    if (tail.startsWith(`/${BOOK}/figures/`)) {
      return route.fulfill({ contentType: "image/png", body: PNG });
    }
    return route.fulfill({ status: 404, body: "{}" });
  });
}

const pdf = { name: "book.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7\n%%EOF") };
const stateOf = (over: Partial<State>): State => ({
  status: "extracting",
  withPages: false,
  inLibrary: true,
  events: () => [STARTED, ...PAGES, SLICE, END("done")],
  calls: [],
  ...over,
});

test.describe("Book Distiller · Ingestion", () => {
  test("the header opens Book Distiller", async ({ page, api }) => {
    await api("healthy");
    await mockBd(page, stateOf({ inLibrary: false }));
    await page.goto("/");
    await page
      .getByRole("navigation", { name: "Console" })
      .getByRole("link", { name: "Book Distiller" })
      .click();
    await expect(page).toHaveURL(/\/bd$/);
    await expect(page.getByRole("heading", { level: 1, name: "Book Distiller" })).toBeVisible();
    await expect(
      page.getByRole("navigation", { name: "Book Distiller stages" }).getByText("Ingestion"),
    ).toBeVisible();
    await expect(page.getByText("No books yet. Upload a PDF or EPUB to start.")).toBeVisible();
  });

  test("upload, then the pages fill in live, then done", async ({ page }) => {
    const state = stateOf({ status: "queued", inLibrary: false });
    state.onCall = (call) => {
      if (call === "upload") state.status = "extracting";
    };
    await mockBd(page, state);
    await page.goto("/bd");

    await page.getByLabel(/Drop a PDF or EPUB here or choose one/).setInputFiles(pdf);
    await expect(page).toHaveURL(new RegExp(`/bd/ingestion\\?book=${BOOK}`));
    await expect(page.getByRole("heading", { level: 2, name: TITLE })).toBeVisible();

    const pages = page.getByRole("list", { name: "Extracted pages" });
    await expect(pages.getByRole("button")).toHaveCount(3);
    await expect(page.getByRole("progressbar", { name: "Pages extracted" })).toHaveAttribute(
      "aria-valuetext",
      "9 of 9 pages",
    );
    await expect(page.getByText("15.0 pages/min")).toBeVisible();
    await expect(page.getByRole("button", { name: "Extract again" })).toBeVisible();
    // the stream was followed to the last page it sent
    const reader = page.getByRole("article", { name: "Page 3" });
    await expect(reader).toContainText("480 ms");
    await expect(reader).toContainText("Foundations of Data Systems › Scalability");
    expect(state.calls).toEqual(["upload"]);
  });

  test("shows the chapter being read, the page chips and the figure of a page", async ({ page }) => {
    await mockBd(page, stateOf({ status: "done", withPages: true }));
    await page.goto(`/bd/ingestion?book=${BOOK}`);

    await page.getByRole("button", { name: /^p\. 2\b/ }).click();
    const reader = page.getByRole("article", { name: "Page 2" });
    await expect(reader).toContainText("Continuing to work correctly");
    await expect(reader.getByRole("list", { name: "What the page contains" })).toContainText("1 picture");
    const figure = reader.getByRole("img", { name: /^Figure p0002/ });
    await expect(figure).toBeVisible();
    await expect.poll(() => figure.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    await expect(page.getByRole("button", { name: /^Foundations of Data Systems/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  test("cancel: stops the run and offers Resume; Resume starts it again", async ({ page }) => {
    const state = stateOf({
      events: () => [STARTED, pageEvent(1), pageEvent(2)], // never ends by itself
    });
    state.onCall = (call) => {
      if (call === "cancel") state.events = () => [STARTED, pageEvent(1), pageEvent(2), END("cancelled")];
      if (call === "run") {
        state.status = "extracting";
        state.events = () => [STARTED, ...PAGES, SLICE, END("done")];
      }
    };
    await mockBd(page, state);
    await page.goto(`/bd/ingestion?book=${BOOK}`);

    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();
    await expect(page.getByText("cancelled", { exact: true }).first()).toBeVisible();
    expect(state.calls).toContain("cancel");

    state.status = "cancelled";
    await page.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByRole("button", { name: "Extract again" })).toBeVisible();
    expect(state.calls).toContain("run");
  });

  test("a failed run says why and can be resumed", async ({ page }) => {
    const message = "the converter ran out of memory on page 4";
    await mockBd(
      page,
      stateOf({ events: () => [STARTED, pageEvent(1), END("failed", message)], status: "extracting" }),
    );
    await page.goto(`/bd/ingestion?book=${BOOK}`);

    await expect(page.getByText(message)).toBeVisible();
    await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();
  });

  test("a reload in the middle of a run restores the pages from the manifest and the replay", async ({
    page,
  }) => {
    await mockBd(
      page,
      stateOf({ withPages: true, events: () => [STARTED, ...PAGES, SLICE] }), // still running
    );
    await page.goto(`/bd/ingestion?book=${BOOK}`);
    const pages = page.getByRole("list", { name: "Extracted pages" });
    await expect(pages.getByRole("button")).toHaveCount(3);

    await page.reload();
    await expect(pages.getByRole("button")).toHaveCount(3);
    await expect(page.getByRole("checkbox", { name: "Follow newest" })).toBeChecked();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible();
  });

  test("deleting a book asks first", async ({ page }) => {
    const state = stateOf({ status: "done", withPages: true });
    await mockBd(page, state);
    await page.route(`**/api/v1/bd/ingestion/${BOOK}`, (route) => {
      if (route.request().method() !== "DELETE") return route.fallback();
      state.calls.push("delete");
      state.inLibrary = false;
      return route.fulfill({ json: { book_id: BOOK, deleted: true } });
    });
    await page.goto("/bd");

    await page.getByRole("button", { name: `Delete ${TITLE}` }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    expect(state.calls).toEqual([]);
    await page.getByRole("button", { name: `Delete ${TITLE}` }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("No books yet. Upload a PDF or EPUB to start.")).toBeVisible();
    expect(state.calls).toEqual(["delete"]);
  });

  const ROUTES: { name: string; url: string; state: () => State; ready: (page: Page) => Promise<void> }[] = [
    {
      name: "empty",
      url: "/bd",
      state: () => stateOf({ inLibrary: false }),
      ready: (page) => expect(page.getByText("No books yet. Upload a PDF or EPUB to start.")).toBeVisible(),
    },
    {
      name: "live",
      url: `/bd/ingestion?book=${BOOK}`,
      state: () => stateOf({ withPages: true, events: () => [STARTED, ...PAGES, SLICE] }),
      ready: (page) => expect(page.getByRole("article", { name: "Page 3" })).toContainText("480 ms"),
    },
    {
      name: "failed",
      url: `/bd/ingestion?book=${BOOK}`,
      state: () => stateOf({ events: () => [STARTED, pageEvent(1), END("failed", "converter error")] }),
      ready: (page) => expect(page.getByText(/The extraction failed: converter error/)).toBeVisible(),
    },
    {
      name: "finished",
      url: `/bd/ingestion?book=${BOOK}`,
      state: () => stateOf({ status: "done", withPages: true }),
      ready: (page) => expect(page.getByRole("button", { name: "Extract again" })).toBeVisible(),
    },
  ];

  for (const scheme of ["dark", "light"] as const) {
    for (const route of ROUTES) {
      test(`${route.name}: no WCAG 2.2 AA violations (${scheme})`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: scheme });
        await mockBd(page, route.state());
        await page.goto(route.url);
        await route.ready(page);
        const { violations } = await new AxeBuilder({ page }).withTags(WCAG).analyze();
        expect(
          violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`),
        ).toEqual([]);
      });
    }
  }

  test("never scrolls sideways and never ships text under 12px", async ({ page }) => {
    await mockBd(page, stateOf({ withPages: true, events: () => [STARTED, ...PAGES, SLICE] }));
    for (const url of ["/bd", `/bd/ingestion?book=${BOOK}`]) {
      await page.goto(url);
      await expect(page.getByRole("heading", { level: 1, name: "Book Distiller" })).toBeVisible();
      if (url.includes("book=")) {
        await expect(page.getByRole("article", { name: "Page 3" })).toBeVisible();
        await page.getByRole("button", { name: /^p\. 2\b/ }).click();
        await expect(page.getByRole("article", { name: "Page 2" })).toBeVisible();
      }
      const { overflow, small } = await page.evaluate(() => {
        const smallText = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
          const hasText = Array.from(element.childNodes).some(
            (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
          );
          // KaTeX lays math out with its own sizes; the floor is for interface text.
          return (
            hasText &&
            !element.closest(".katex") &&
            Number.parseFloat(getComputedStyle(element).fontSize) < 12
          );
        });
        return {
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          small: smallText.map((element) => `${element.tagName}.${element.className}`),
        };
      });
      expect(overflow, `${url} scrolls sideways`).toBeLessThanOrEqual(0);
      expect(small, `${url} has text under 12px`).toEqual([]);
    }
  });
});
