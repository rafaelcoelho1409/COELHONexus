// @vitest-environment jsdom
// @vitest-environment-options { "url": "http://nexus.test" }
// The page is served from the API's own origin, as in the browser (the app proxies `/api`): the upload goes through
// XMLHttpRequest, and a cross-origin one would need a CORS preflight that does not exist in production.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { BookRow, Snapshot } from "@/shared/api";
import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import type { AgenticProgress } from "../entities";
import { agenticRunStatusSchema } from "../entities";
import { AUTO_CHAIN_STORAGE_KEY, LAST_PAGE_STORAGE_PREFIX, READER_LAYOUT_STORAGE_KEY } from "../params";
import {
  agenticRun,
  BOOK_ID,
  BOOK_TITLE,
  bookRow,
  pageEntry,
  pageEvent,
  sliceDone,
  snapshot,
  sse,
  startedEvent,
  terminalEvent,
} from "../test-fixtures";
import { IngestionStage } from "./ingestion-stage";
import { BdOverlaysProvider } from "./overlays";

const ORIGIN = "http://nexus.test";
const API = `${ORIGIN}/api/v1/bd/ingestion`;

const push = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  usePathname: () => "/bd/ingestion",
  useSearchParams: () => new URLSearchParams(search),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  push.mockClear();
  window.localStorage.removeItem(AUTO_CHAIN_STORAGE_KEY);
  window.localStorage.removeItem(READER_LAYOUT_STORAGE_KEY);
  window.localStorage.removeItem(`${LAST_PAGE_STORAGE_PREFIX}${BOOK_ID}`);
  client.setConfig({ baseUrl: ORIGIN });
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderStage(book?: string) {
  search = book ? `book=${book}` : "";
  return render(
    <QueryProvider>
      <BdOverlaysProvider>
        <IngestionStage />
      </BdOverlaysProvider>
    </QueryProvider>,
  );
}

async function openPipeline() {
  await userEvent.click(await screen.findByRole("tab", { name: "Pipeline" }));
}

async function openMaterials() {
  await userEvent.click(await screen.findByRole("tab", { name: "Materials" }));
}

/** An event stream the test pushes into, as the server does while a run is live. */
function liveStream() {
  const encoder = new TextEncoder();
  let send: (event: unknown) => void = () => {};
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      send = (event) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
    },
  });
  return {
    response: new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } }),
    send: (event: unknown) => send(event),
  };
}

function serveLibrary(rows: BookRow[] = []) {
  server.use(http.get(API, () => HttpResponse.json(rows)));
}

function serveBook(snap: Snapshot, events?: () => Response) {
  server.use(http.get(`${API}/${BOOK_ID}`, () => HttpResponse.json(snap)));
  if (events) server.use(http.get(`${API}/${BOOK_ID}/events`, events));
}

const queued = { status: "queued", book_id: BOOK_ID, run_id: "run-2", resumed: false };

function pdf(name = "book.pdf", size?: number): File {
  const file = new File(["%PDF-1.7"], name, { type: "application/pdf" });
  if (size !== undefined) Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("Library", () => {
  it("lists the books newest first with a status in words and the pages done", async () => {
    serveLibrary([
      bookRow({ book_id: "old", title: "An Older Book", status: "done", pages_done: 9, created_at: 1 }),
      bookRow({ book_id: "new", title: "A Newer Book", status: "extracting", pages_done: 3, created_at: 9 }),
      bookRow({ book_id: "bad", title: "A Broken Book", status: "failed", pages_done: 2, created_at: 5 }),
    ]);
    renderStage();

    await userEvent.click(await screen.findByRole("button", { name: "Library · 3" }));
    const list = await screen.findByRole("list", { name: "Ingested books" });
    const items = within(list).getAllByRole("listitem");
    const firstItem = items[0];
    if (!firstItem) throw new Error("Expected the newest library option.");
    expect(firstItem).toHaveTextContent("A Newer Book");
    expect(items[1]).toHaveTextContent("A Broken Book");
    expect(items[2]).toHaveTextContent("An Older Book");
    expect(firstItem).toHaveClass("grid-cols-[minmax(0,1fr)_auto_auto]");
    expect(within(firstItem).getByRole("button", { name: /^A Newer Book/ })).toHaveClass("min-w-0");
    expect(firstItem).toHaveTextContent("extracting");
    expect(firstItem).toHaveTextContent("3/9 pages");
    expect(firstItem).toHaveTextContent("PDF · 3/9 pages · 18 MB");
    expect(items[1]).toHaveTextContent("failed");
    expect(within(list).getByRole("button", { name: "Refresh A Newer Book" })).toBeDisabled();
    expect(within(list).getByRole("button", { name: "Refresh An Older Book" })).toBeEnabled();
    await userEvent.click(within(list).getByRole("button", { name: /^A Newer Book/ }));
    expect(push).toHaveBeenCalledWith("/bd/ingestion?book=new", { scroll: false });
  });

  it("shows EPUB format, section count and file size in a library option", async () => {
    serveLibrary([
      bookRow({
        filename: "field-notes.epub",
        size_bytes: 1_536,
        n_pages: 2,
        pages_done: 1,
      }),
    ]);
    renderStage();

    await userEvent.click(await screen.findByRole("button", { name: "Library · 1" }));
    const list = await screen.findByRole("list", { name: "Ingested books" });
    expect(within(list).getByRole("listitem")).toHaveTextContent("EPUB · 1/2 sections · 1.5 KB");
  });

  it("filters the searchable book picker and closes it with Escape", async () => {
    serveLibrary([
      bookRow({ title: "The Quiet Archive", filename: "quiet-archive.epub" }),
      bookRow({ book_id: "second", title: "Field Notes", filename: "field-notes.pdf" }),
    ]);
    renderStage();

    const trigger = await screen.findByRole("button", { name: "Library · 2" });
    await userEvent.click(trigger);
    const search = screen.getByRole("searchbox", { name: "Search books" });
    await userEvent.type(search, "field-notes.pdf");

    const list = screen.getByRole("list", { name: "Ingested books" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByRole("button", { name: /^Field Notes/ })).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: /The Quiet Archive/ })).not.toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("says so when the library is empty, loading or unavailable", async () => {
    serveLibrary([]);
    const { unmount } = renderStage();
    await userEvent.click(await screen.findByRole("button", { name: "Library · 0" }));
    expect(await screen.findByText(/No books yet/)).toBeInTheDocument();
    expect(screen.getByText("No book open.")).toBeInTheDocument();
    unmount();

    server.use(http.get(API, () => new HttpResponse(null, { status: 503 })));
    renderStage();
    await userEvent.click(await screen.findByRole("button", { name: "Library" }));
    expect(await screen.findByText("The library could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("deletes a book after a confirmation, and not before", async () => {
    const deleted: string[] = [];
    serveLibrary([bookRow({ status: "done", pages_done: 9 })]);
    server.use(
      http.delete(`${API}/${BOOK_ID}`, () => {
        deleted.push(BOOK_ID);
        return HttpResponse.json({ book_id: BOOK_ID, deleted: true });
      }),
    );
    renderStage();

    await userEvent.click(await screen.findByRole("button", { name: "Library · 1" }));
    await userEvent.click(await screen.findByRole("button", { name: `Delete ${BOOK_TITLE}` }));
    const dialog = await screen.findByRole("dialog");
    expect(deleted).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(deleted).toEqual([]);

    await userEvent.click(screen.getByRole("button", { name: "Library · 1" }));
    await userEvent.click(screen.getByRole("button", { name: `Delete ${BOOK_TITLE}` }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleted).toEqual([BOOK_ID]));
    expect(await screen.findByText(/^Deleted /)).toBeInTheDocument();
  });

  it("relays the server's refusal to delete, and disables delete for a running book", async () => {
    serveLibrary([
      bookRow({ status: "done" }),
      bookRow({ book_id: "live", title: "Live One", status: "extracting" }),
    ]);
    server.use(
      http.delete(`${API}/${BOOK_ID}`, () =>
        HttpResponse.json({ book_id: BOOK_ID, deleted: false, reason: "a run holds this book" }),
      ),
    );
    renderStage();

    await userEvent.click(await screen.findByRole("button", { name: "Library · 2" }));
    expect(await screen.findByRole("button", { name: "Delete Live One" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: `Delete ${BOOK_TITLE}` }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("a run holds this book")).toBeInTheDocument();
  });
});

describe("Upload", () => {
  const choose = () => screen.getByLabelText("Upload book");

  it("refuses an unsupported file, or one over 300 MB, without sending anything", async () => {
    serveLibrary();
    renderStage();
    const user = userEvent.setup({ applyAccept: false });

    await user.upload(choose(), new File(["hi"], "notes.txt", { type: "text/plain" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only PDF or EPUB files can be uploaded.");

    await user.upload(choose(), pdf("huge.pdf", 301 * 1024 * 1024));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/over 300 MB/));
    expect(push).not.toHaveBeenCalled();
  });

  it("opens the book when the upload is queued", async () => {
    serveLibrary();
    server.use(http.post(API, () => HttpResponse.json(queued)));
    renderStage();

    await userEvent.upload(choose(), pdf());
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(`/bd/ingestion?book=${BOOK_ID}`, { scroll: false }),
    );
    expect(await screen.findByText("Extraction queued.")).toBeInTheDocument();
  });

  it("accepts an EPUB whose browser media type is generic", async () => {
    serveLibrary();
    server.use(http.post(API, () => HttpResponse.json(queued)));
    renderStage();

    const epub = new File(["PK"], "novel.epub", { type: "application/octet-stream" });
    await userEvent.setup({ applyAccept: false }).upload(choose(), epub);
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(`/bd/ingestion?book=${BOOK_ID}`, { scroll: false }),
    );
    expect(await screen.findByText("Extraction queued.")).toBeInTheDocument();
  });

  it("persists the automatic chain preference for future runs", async () => {
    serveLibrary();
    renderStage();

    await userEvent.click(screen.getByRole("checkbox", { name: "Run Agentic AI after extraction" }));
    expect(window.localStorage.getItem(AUTO_CHAIN_STORAGE_KEY)).toBe("true");
  });

  it("opens a cached book and says it was already extracted", async () => {
    serveLibrary();
    server.use(http.post(API, () => HttpResponse.json({ ...queued, status: "cached", run_id: null })));
    renderStage();

    await userEvent.upload(choose(), pdf());
    expect(await screen.findByText("Already extracted — opened it.")).toBeInTheDocument();
    expect(push).toHaveBeenCalledWith(`/bd/ingestion?book=${BOOK_ID}`, { scroll: false });
  });

  it("opens a locked book to follow its live run", async () => {
    serveLibrary();
    server.use(http.post(API, () => HttpResponse.json({ ...queued, status: "locked" })));
    renderStage();

    await userEvent.upload(choose(), pdf());
    expect(await screen.findByText(/already being extracted — following the live run/)).toBeInTheDocument();
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("says it resumed a stopped extraction", async () => {
    serveLibrary();
    server.use(http.post(API, () => HttpResponse.json({ ...queued, resumed: true })));
    renderStage();

    await userEvent.upload(choose(), pdf());
    expect(await screen.findByText(/Resuming the extraction where it stopped/)).toBeInTheDocument();
  });

  it("explains a 413 and a 400 from the server", async () => {
    serveLibrary();
    server.use(http.post(API, () => HttpResponse.json({ detail: "payload too large" }, { status: 413 })));
    renderStage();

    await userEvent.upload(choose(), pdf());
    expect(await screen.findByRole("alert")).toHaveTextContent("That file is over 300 MB.");

    server.use(
      http.post(API, () => HttpResponse.json({ detail: "this is not a readable PDF" }, { status: 400 })),
    );
    await userEvent.upload(choose(), pdf("second.pdf"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("this is not a readable PDF"));
    expect(push).not.toHaveBeenCalled();
  });
});

describe("A book being extracted", () => {
  it("shows EPUB reader units as sections rather than invented pages", async () => {
    const epubName = "field-notes.epub";
    serveLibrary([bookRow({ filename: epubName, n_pages: 2, pages_done: 1, status: "done" })]);
    serveBook(snapshot({ filename: epubName, n_pages: 2, status: "done", pages: [pageEntry(1)] }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "# Opening\n\nThe opening section." }),
      ),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    expect(await screen.findByRole("progressbar", { name: "Sections extracted" })).toHaveAttribute(
      "aria-valuetext",
      "1 of 2 sections",
    );
    await userEvent.click(screen.getByRole("tab", { name: "Materials" }));
    await userEvent.click(screen.getByRole("button", { name: BOOK_TITLE }));
    expect(await screen.findByText("EPUB · 1/2 sections · 18 MB")).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Section 1" })).toHaveTextContent(
      "The opening section.",
    );
    expect(screen.getByRole("list", { name: "Extracted sections" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reading mode" }));
    expect(await screen.findByRole("dialog", { name: BOOK_TITLE })).toHaveTextContent("Section 1 / 2");
  });

  it("searches extracted units by section title and number", async () => {
    const rows = [pageEntry(1), pageEntry(2), pageEntry(3)];
    serveLibrary([bookRow({ status: "done", pages_done: 3 })]);
    serveBook(snapshot({ status: "done", n_pages: 3, pages: rows }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "Opening page." }),
      ),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    const pages = await screen.findByRole("list", { name: "Extracted pages" });
    const search = screen.getByRole("searchbox", { name: "Search pages" });
    await userEvent.type(search, "Scalability");
    expect(within(pages).getAllByRole("button")).toHaveLength(1);
    expect(within(pages).getByRole("button", { name: /^p\. 3\b/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Showing 1 of 3 pages");

    await userEvent.clear(search);
    await userEvent.type(search, "unknown");
    expect(screen.getByText("No pages match that search.")).toBeInTheDocument();
  });

  it("searches the full extracted text and opens the matching page", async () => {
    const rows = [pageEntry(1), pageEntry(2)];
    serveLibrary([bookRow({ status: "done", pages_done: 2 })]);
    serveBook(snapshot({ status: "done", n_pages: 2, pages: rows }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "# Opening\n\nThe first passage." }),
      ),
      http.get(`${API}/${BOOK_ID}/pages/2`, () =>
        HttpResponse.json({
          book_id: BOOK_ID,
          page: 2,
          markdown: "## Storage\n\nA durable record survives failure.",
        }),
      ),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    await userEvent.click(await screen.findByRole("button", { name: /Search book/ }));
    const dialog = await screen.findByRole("dialog", { name: "Search this book" });
    const search = within(dialog).getByRole("combobox", {
      name: "Search titles, headings, and extracted text",
    });
    await userEvent.type(search, "durable record");
    const result = await within(dialog).findByRole("option", {
      name: /Page\s*2\s*Reliability\s*Match in the text/,
    });
    await userEvent.click(result);

    expect(await screen.findByRole("article", { name: "Page 2" })).toHaveTextContent(
      "A durable record survives failure.",
    );
  });

  it("opens a focused reader with page navigation and progress-friendly content flow", async () => {
    const rows = [pageEntry(1), pageEntry(2)];
    serveLibrary([bookRow({ status: "done", pages_done: 2 })]);
    serveBook(snapshot({ status: "done", n_pages: 2, pages: rows }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "# Opening\n\nFirst extracted page." }),
      ),
      http.get(`${API}/${BOOK_ID}/pages/2`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 2, markdown: "## Next\n\nSecond extracted page." }),
      ),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    await userEvent.click(await screen.findByRole("button", { name: "Reading mode" }));
    const dialog = await screen.findByRole("dialog", { name: BOOK_TITLE });
    expect(within(dialog).getByText("Page 1 / 2")).toBeInTheDocument();
    expect(within(dialog).getByRole("article", { name: "Page 1" })).toHaveTextContent(
      "First extracted page.",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Next page" }));
    expect(await within(dialog).findByRole("article", { name: "Page 2" })).toHaveTextContent(
      "Second extracted page.",
    );
    expect(within(dialog).getByText("Page 2 / 2")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Exit reading mode" }));
    expect(screen.queryByRole("dialog", { name: BOOK_TITLE })).not.toBeInTheDocument();
  });

  it("switches to raw Markdown and keeps large figure galleries collapsed until opened", async () => {
    const figures = ["a.png", "b.png", "c.png", "d.png", "e.png"];
    serveLibrary([bookRow({ status: "done", pages_done: 1 })]);
    serveBook(
      snapshot({ status: "done", pages: [pageEntry(2, { figures, pictures: figures.length })] }, null),
    );
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/2`, () =>
        HttpResponse.json({
          book_id: BOOK_ID,
          page: 2,
          markdown: "## Reliability\n\n<!-- image -->\n\nExtracted paragraph.",
        }),
      ),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    const reader = await screen.findByRole("article", { name: "Page 2" });
    expect(await within(reader).findByText("Extracted paragraph.")).toBeInTheDocument();
    await userEvent.click(within(reader).getByRole("button", { name: "Markdown source" }));
    expect(within(reader).getByRole("region", { name: "Markdown source" })).toHaveTextContent(
      "<!-- image -->",
    );

    expect(within(reader).getByRole("figure", { name: "Figure 1 from page 2" })).toBeInTheDocument();
    await userEvent.click(within(reader).getByRole("button", { name: "View figure 1 from page 2 (a.png)" }));
    const viewer = await screen.findByRole("dialog", { name: "Figure 1 of 5" });
    await userEvent.click(within(viewer).getByRole("button", { name: "Next figure" }));
    expect(within(viewer).getByRole("heading", { name: "Figure 2 of 5" })).toBeInTheDocument();
    await userEvent.click(within(viewer).getByRole("button", { name: "Close figure viewer" }));

    const summary = within(reader).getByText("Figure gallery · 5");
    const gallery = summary.closest("details");
    expect(gallery).not.toHaveAttribute("open");
    await userEvent.click(summary);
    expect(gallery).toHaveAttribute("open");
    expect(within(reader).getAllByRole("figure")).toHaveLength(5);
  });

  it("fills in live: progress, throughput, chapters, pages, the reader, figures", async () => {
    const stream = liveStream();
    serveLibrary([bookRow()]);
    serveBook(snapshot({ status: "extracting" }, { status: "running" }), () => stream.response);
    renderStage(BOOK_ID);

    expect(await screen.findByRole("heading", { name: BOOK_TITLE, level: 2 })).toBeInTheDocument();
    stream.send(startedEvent);
    stream.send(pageEvent(1));
    stream.send(pageEvent(2));
    stream.send(sliceDone);

    await openMaterials();
    await screen.findByRole("article", { name: "Page 2" });
    const pages = screen.getByRole("list", { name: "Extracted pages" });
    expect(within(pages).getAllByRole("button")).toHaveLength(2);
    // following: the reader shows the newest page, its section path, its counts and its figure
    const reader = screen.getByRole("article", { name: "Page 2" });
    expect(reader).toHaveTextContent("Continuing to work correctly");
    expect(reader).toHaveTextContent("Foundations of Data Systems › Reliability");
    expect(reader).toHaveTextContent("1 picture");
    await userEvent.click(within(reader).getByText("Figure gallery · 1"));
    const figure = within(reader).getByRole("button", {
      name: "View figure 1 from page 2 (p0002-1.png)",
    });
    expect(figure.querySelector("img")).toHaveAttribute(
      "src",
      expect.stringContaining(`/api/v1/bd/ingestion/${BOOK_ID}/figures/p0002-1.png`),
    );
    // the chapter being read is marked
    expect(screen.getByRole("button", { name: /^Foundations of Data Systems/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
    await openPipeline();
    const bar = await screen.findByRole("progressbar", { name: "Pages extracted" });
    await waitFor(() => expect(bar).toHaveAttribute("aria-valuetext", "2 of 9 pages"));
    expect(screen.getByText("15.0 pages/min")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Docling extraction progress" })).getByRole("button", {
        name: "Stop Docling extraction",
      }),
    ).toBeEnabled();
    await userEvent.click(screen.getByRole("tab", { name: "Materials" }));
    expect(screen.getByRole("list", { name: "Extracted pages" })).toBeInTheDocument();
  });

  it("follows the newest page until the reader picks one, and again when asked", async () => {
    const stream = liveStream();
    serveLibrary([bookRow()]);
    serveBook(snapshot({ status: "extracting" }), () => stream.response);
    renderStage(BOOK_ID);
    await screen.findByRole("heading", { name: BOOK_TITLE, level: 2 });

    await openMaterials();
    stream.send(startedEvent);
    stream.send(pageEvent(1));
    stream.send(pageEvent(2));
    await screen.findByRole("article", { name: "Page 2" });

    await userEvent.click(screen.getByRole("button", { name: /^p\. 1\b/ }));
    expect(screen.getByRole("article", { name: "Page 1" })).toHaveTextContent("Foundations of Data Systems");
    expect(screen.getByRole("checkbox", { name: "Follow newest" })).not.toBeChecked();

    stream.send(pageEvent(3));
    await waitFor(() => expect(screen.getByRole("button", { name: /^p\. 3\b/ })).toBeInTheDocument());
    expect(screen.getByRole("article", { name: "Page 1" })).toBeInTheDocument(); // not dragged along

    await userEvent.click(screen.getByRole("checkbox", { name: "Follow newest" }));
    expect(screen.getByRole("article", { name: "Page 3" })).toHaveTextContent("Percentile");
  });

  it("ends on done without a standalone refresh button", async () => {
    serveLibrary([bookRow({ status: "done", pages_done: 9 })]);
    serveBook(snapshot({ status: "extracting" }), () =>
      sse([startedEvent, pageEvent(1), pageEvent(2), pageEvent(3), sliceDone, terminalEvent("done")]),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    expect(await screen.findByRole("progressbar", { name: "Pages extracted" })).toHaveAttribute(
      "aria-valuetext",
      "9 of 9 pages",
    );
    expect(screen.queryByRole("button", { name: "Extract again" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: BOOK_TITLE }));
    expect(screen.getByRole("button", { name: `Refresh ${BOOK_TITLE}` })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: BOOK_TITLE }));
    expect(
      within(screen.getByRole("region", { name: "Docling extraction progress" })).getByRole("button", {
        name: "Stop Docling extraction",
      }),
    ).toBeDisabled();
    await userEvent.click(screen.getByRole("tab", { name: "Materials" }));
    expect(screen.queryByRole("checkbox", { name: "Follow newest" })).not.toBeInTheDocument();
  });

  it("restores the picture after a reload from the manifest and the replayed history, without duplicates", async () => {
    serveLibrary([bookRow()]);
    serveBook(
      snapshot(
        { status: "extracting", pages: [pageEntry(1), pageEntry(2)] },
        { status: "running", completed: 2 },
      ),
      () => sse([startedEvent, pageEvent(1), pageEvent(2), pageEvent(3), sliceDone]),
    );
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/:page`, ({ params }) =>
        HttpResponse.json({ book_id: BOOK_ID, page: Number(params.page), markdown: `Page ${params.page}` }),
      ),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    await screen.findByRole("article", { name: "Page 3" });
    expect(within(screen.getByRole("list", { name: "Extracted pages" })).getAllByRole("button")).toHaveLength(
      3,
    );
    expect(screen.getByRole("checkbox", { name: "Follow newest" })).toBeChecked();
    await openPipeline();
    expect(
      within(screen.getByRole("region", { name: "Docling extraction progress" })).getByRole("button", {
        name: "Stop Docling extraction",
      }),
    ).toBeInTheDocument();
  });

  it("reads the whole page when the stream cut it", async () => {
    const fetched: string[] = [];
    serveLibrary([bookRow()]);
    serveBook(snapshot({ status: "extracting" }), () =>
      sse([startedEvent, pageEvent(1, { markdown: "The beginning of a long page…", truncated: true })]),
    );
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () => {
        fetched.push("1");
        return HttpResponse.json({
          book_id: BOOK_ID,
          page: 1,
          markdown: "The beginning of a long page, and its very long end.",
        });
      }),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    expect(await screen.findByText(/and its very long end/)).toBeInTheDocument();
    expect(fetched).toEqual(["1"]);
  });

  it("switches an extracted page to its AI version as soon as repair finishes", async () => {
    const stream = liveStream();
    let pageReads = 0;
    const progress: AgenticProgress = {
      current_node: "repair_pages",
      node_status: { audit_batch: "done", repair_pages: "running" },
      audited_pages: [1],
      audit_warning_pages: [1],
      processed_pages: [],
      active_audit_pages: [],
      active_repair_pages: [1],
    };
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(
      snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null, {
        book_id: BOOK_ID,
        extraction_run_id: "extract-1",
        agentic_run_id: "agentic-1",
        status: "running",
        expected_page_count: 1,
        published_page_count: 0,
        quality_status: null,
        delivered_page_count: 0,
        discarded_blank_page_count: 0,
        review_page_count: 0,
        review_pages: [],
        unverified_page_count: 0,
        unverified_pages: [],
        discarded_pages: [],
        image_only_pages: [],
        progress,
        created_at: 1,
        updated_at: 2,
        finished_at: null,
        error: null,
      }),
    );
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () => {
        pageReads += 1;
        return HttpResponse.json({
          book_id: BOOK_ID,
          page: 1,
          markdown: pageReads === 1 ? "Docling body" : "AI repaired body",
          content_source: pageReads === 1 ? "docling" : "agentic",
        });
      }),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/events`, () => stream.response),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
        HttpResponse.json({
          total: { calls: 1, tokens_in: 20, tokens_out: 8, reasoning_tokens: 2, errors: 0 },
          by_node: {
            page_auditor: {
              calls: 1,
              tokens_in: 20,
              tokens_out: 8,
              reasoning_tokens: 2,
              errors: 0,
              by_model: {
                "vendor-a/model-x": {
                  calls: 1,
                  tokens_in: 20,
                  tokens_out: 8,
                  reasoning_tokens: 2,
                },
              },
            },
          },
        }),
      ),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    const reader = await screen.findByRole("article", { name: "Page 1" });
    expect(await within(reader).findByText("Docling body")).toBeInTheDocument();
    expect(within(reader).getByText("Docling extraction")).toBeInTheDocument();
    expect(screen.getByText("Audit warnings on 1 page.")).toBeInTheDocument();
    stream.send({
      kind: "page_repair",
      page: 1,
      status: "done",
      progress: { ...progress, processed_pages: [1], active_repair_pages: [] },
    });

    expect(await within(reader).findByText("AI repaired body")).toBeInTheDocument();
    expect(within(reader).getByText("AI-processed content")).toBeInTheDocument();
    expect(pageReads).toBe(2);
    expect(screen.queryByRole("progressbar", { name: "AI-processed pages" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Agentic pipeline" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Pipeline" }));
    expect(screen.getByRole("progressbar", { name: "AI-processed pages" })).toHaveAttribute(
      "aria-valuenow",
      "1",
    );
    const pipeline = screen.getByRole("region", { name: "Agentic pipeline stages" });
    await userEvent.click(within(pipeline).getByRole("button", { name: "List" }));
    await userEvent.click(within(pipeline).getByRole("button", { name: /Audit pages/ }));
    const nodeDetails = await screen.findByRole("dialog", { name: "Ingestion LLM usage" });
    expect(nodeDetails).toHaveTextContent("Audit pages");
    expect(nodeDetails).toHaveTextContent("Input tokens");
    expect(nodeDetails).toHaveTextContent("20");
    const nodeUsage = within(nodeDetails).getByRole("region", { name: "Usage by agent and model" });
    expect(nodeUsage).toHaveTextContent("vendor-a");
    expect(nodeUsage).toHaveTextContent("model-x");
    expect(nodeUsage).toHaveTextContent("Output tokens");
    await userEvent.click(within(nodeDetails).getByRole("button", { name: "By model" }));
    expect(within(nodeDetails).getByRole("region", { name: "Usage by model" })).toHaveTextContent("vendor-a");
    await userEvent.click(within(nodeDetails).getByRole("button", { name: "By agent" }));
    expect(within(nodeDetails).getByRole("region", { name: "Usage by agent" })).toHaveTextContent(
      "Page auditor",
    );
    expect(within(nodeDetails).queryByText("Provider")).not.toBeInTheDocument();
    await userEvent.click(within(nodeDetails).getByRole("button", { name: "By agent + model" }));
    await userEvent.click(within(nodeDetails).getByRole("button", { name: "Close node usage" }));
    const progressBox = screen.getByRole("region", { name: "Agentic AI progress" });
    expect(within(progressBox).queryByRole("button", { name: "LLM usage" })).not.toBeInTheDocument();
    expect(within(progressBox).queryByRole("button", { name: "Pipeline" })).not.toBeInTheDocument();
    await userEvent.click(within(pipeline).getByRole("button", { name: "LLM usage" }));
    const usageSheet = await screen.findByRole("dialog", { name: "Ingestion LLM usage" });
    const usageByModel = within(usageSheet).getByRole("region", { name: "Usage by agent and model" });
    expect(usageByModel).toHaveTextContent("vendor-a");
    expect(usageByModel).toHaveTextContent("model-x");
    expect(usageByModel).toHaveTextContent("Page auditor");
    expect(usageByModel).toHaveTextContent("20");
  });

  it("shows extraction progress inside Pipeline, not Materials", async () => {
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(
      snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null, {
        book_id: BOOK_ID,
        extraction_run_id: "extract-1",
        agentic_run_id: "agentic-1",
        status: "done",
        expected_page_count: 1,
        published_page_count: 1,
        quality_status: "complete",
        delivered_page_count: 1,
        discarded_blank_page_count: 0,
        review_page_count: 0,
        review_pages: [],
        unverified_page_count: 0,
        unverified_pages: [],
        discarded_pages: [],
        image_only_pages: [],
        progress: {
          current_node: null,
          node_status: {
            audit_batch: "done",
            plan_repairs: "done",
            repair_pages: "done",
            normalize_metadata: "done",
            publish: "done",
          },
          audited_pages: [1],
          audit_warning_pages: [],
          processed_pages: [1],
          active_audit_pages: [],
          active_repair_pages: [],
        },
        created_at: 1,
        updated_at: 2,
        finished_at: 3,
        error: null,
      }),
    );
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({
          book_id: BOOK_ID,
          page: 1,
          markdown: "AI-repaired first page",
          content_source: "agentic",
        }),
      ),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
        HttpResponse.json({
          total: { calls: 4, tokens_in: 120, tokens_out: 45, reasoning_tokens: 8, errors: 0 },
          by_node: {},
        }),
      ),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    expect(screen.getByRole("tab", { name: "Pipeline" })).toHaveAttribute("aria-selected", "true");
    const extractedProgress = screen.getByRole("progressbar", { name: "Pages extracted" });
    const agenticProgress = screen.getByRole("progressbar", { name: "AI-processed pages" });
    expect(extractedProgress).toHaveAttribute("aria-valuetext", "1 of 1 pages");
    expect(agenticProgress).toHaveAttribute("aria-valuenow", "1");
    expect(screen.getAllByRole("img", { name: "Completed successfully" })).toHaveLength(2);
    expect(screen.getByText("Quality checks passed · 1 delivered · 0 blank excluded")).toBeInTheDocument();
    const pipelineUsage = screen.getByRole("region", { name: "Pipeline usage" });
    expect(pipelineUsage).toHaveTextContent("4");
    expect(pipelineUsage.querySelector("dl")).toHaveClass("grid-cols-1");
    expect(pipelineUsage.querySelector("dl")).not.toHaveClass("grid-cols-2");
    const agenticPipeline = screen.getByRole("region", { name: "Agentic pipeline stages" });
    expect(agenticPipeline).toHaveTextContent("Publish results");
    expect(within(agenticPipeline).getByRole("region", { name: "Chapter progress" })).toBeInTheDocument();
    expect(extractedProgress).toBeInTheDocument();
    expect(agenticProgress).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Page 1" })).not.toBeInTheDocument();

    await openMaterials();
    expect(
      await within(screen.getByRole("article", { name: "Page 1" })).findByText("AI-repaired first page"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Pages extracted" })).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "AI-processed pages" })).not.toBeInTheDocument();
  });

  it("shows the finished check on a run that needs review, with the quality result beside it", async () => {
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    const reviewedRun = agenticRun({
      quality_status: "complete_with_review",
      delivered_page_count: 1,
      discarded_blank_page_count: 0,
      review_page_count: 1,
    });
    expect(agenticRunStatusSchema.parse(reviewedRun).quality_status).toBe("complete_with_review");
    serveBook(snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null, reviewedRun));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "AI page", content_source: "agentic" }),
      ),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
        HttpResponse.json({
          total: { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 },
          by_node: {},
        }),
      ),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    const progressBox = screen.getByRole("region", { name: "Agentic AI progress" });
    expect(
      within(progressBox).getByText(
        "Run needs attention · 1 page needs review · 0 audits unverified · 1 delivered · 0 blank excluded",
      ),
    ).toBeInTheDocument();
    expect(within(progressBox).getByRole("img", { name: "Completed successfully" })).toBeInTheDocument();
  });

  it("shows no finished check on an agentic run that did not finish", async () => {
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(
      snapshot(
        { status: "done", n_pages: 1, pages: [pageEntry(1)] },
        null,
        agenticRun({ status: "cancelled", quality_status: null }),
      ),
    );
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "AI page", content_source: "agentic" }),
      ),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
        HttpResponse.json({
          total: { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 },
          by_node: {},
        }),
      ),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    const progressBox = screen.getByRole("region", { name: "Agentic AI progress" });
    expect(
      within(progressBox).queryByRole("img", { name: "Completed successfully" }),
    ).not.toBeInTheDocument();
  });

  it("shows the agentic graph and selectable nodes before an Agentic AI run exists", async () => {
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "Extracted page" }),
      ),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    expect(screen.getByRole("region", { name: "Pipeline usage" })).toHaveTextContent(
      "No Agentic AI run yet.",
    );
    expect(screen.getByRole("tab", { name: "Pipeline" })).toHaveAttribute("aria-selected", "true");
    expect(
      within(screen.getByRole("region", { name: "Agentic pipeline stages" })).getByRole("region", {
        name: "Chapter progress",
      }),
    ).toBeInTheDocument();
    await openPipeline();
    const pipeline = screen.getByRole("region", { name: "Agentic pipeline stages" });
    expect(within(pipeline).getByRole("button", { name: "Graph" })).toBeInTheDocument();
    await userEvent.click(within(pipeline).getByRole("button", { name: "List" }));
    const auditNode = within(pipeline).getByRole("button", { name: /Audit pages/ });
    expect(auditNode).toHaveTextContent("pending");
    await userEvent.click(within(pipeline).getByRole("button", { name: "Graph" }));
    expect(within(pipeline).getByRole("button", { name: /Audit pages/ })).toBeInTheDocument();
  });

  it("lets the user start Agentic AI separately after extraction", async () => {
    const requests: string[] = [];
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "Extracted page" }),
      ),
      http.post(`${API}/${BOOK_ID}/agentic/runs`, ({ request }) => {
        requests.push(request.method);
        return HttpResponse.json({
          status: "queued",
          book_id: BOOK_ID,
          extraction_run_id: "extract-1",
          agentic_run_id: "agentic-1",
        });
      }),
    );
    renderStage(BOOK_ID);

    expect(screen.queryByRole("dialog", { name: "Agentic pipeline" })).not.toBeInTheDocument();
    await openPipeline();
    await userEvent.click(await screen.findByRole("button", { name: "Start Agentic AI" }));

    await waitFor(() => expect(requests).toEqual(["POST"]));
    expect(await screen.findByText("Agentic ingestion queued.")).toBeInTheDocument();
  });

  it("starts a fresh candidate after a completed Agentic run while preserving its history", async () => {
    const requests: string[] = [];
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null, agenticRun()));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "Previous AI result" }),
      ),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
        HttpResponse.json({ total: {}, by_node: {} }),
      ),
      http.post(`${API}/${BOOK_ID}/agentic/runs`, ({ request }) => {
        requests.push(request.method);
        return HttpResponse.json({
          status: "queued",
          book_id: BOOK_ID,
          extraction_run_id: "extract-1",
          agentic_run_id: "agentic-2",
        });
      }),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    const start = await screen.findByRole("button", { name: "Start Agentic AI" });
    expect(start).toBeEnabled();
    expect(start).toHaveAttribute(
      "title",
      expect.stringContaining("completed run and its artifacts are preserved"),
    );
    await userEvent.click(start);

    await waitFor(() => expect(requests).toEqual(["POST"]));
    expect(await screen.findByText("Agentic ingestion queued.")).toBeInTheDocument();
  });

  it("stops Agentic AI cooperatively and offers Resume after the terminal event", async () => {
    const stream = liveStream();
    const requests: string[] = [];
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(
      snapshot(
        { status: "done", n_pages: 1, pages: [pageEntry(1)] },
        null,
        agenticRun({ status: "running", finished_at: null, updated_at: 3 }),
      ),
      () => stream.response,
    );
    server.use(
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/events`, () => stream.response),
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "Extracted page" }),
      ),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
        HttpResponse.json({ total: {}, by_node: {} }),
      ),
      http.post(`${API}/${BOOK_ID}/agentic/runs/agentic-1/cancel`, ({ request }) => {
        requests.push(request.method);
        return HttpResponse.json({
          book_id: BOOK_ID,
          agentic_run_id: "agentic-1",
          status: "cancelling",
        });
      }),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    await userEvent.click(await screen.findByRole("button", { name: "Stop Agentic AI" }));
    expect(requests).toEqual(["POST"]);
    expect(await screen.findByText(/Stop requested; active page work is draining/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop Agentic AI" })).toBeDisabled();

    stream.send({ kind: "run_cancelled", status: "cancelled" });
    expect(await screen.findByRole("button", { name: "Resume Agentic AI" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Wipe Agentic AI" })).toBeEnabled();
  });

  it("wipes only Agentic artifacts after confirmation", async () => {
    const requests: string[] = [];
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null, agenticRun()));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "Extracted page" }),
      ),
      http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
        HttpResponse.json({ total: {}, by_node: {} }),
      ),
      http.delete(`${API}/${BOOK_ID}/agentic/runs/agentic-1`, ({ request }) => {
        requests.push(request.method);
        return HttpResponse.json({
          book_id: BOOK_ID,
          agentic_run_id: "agentic-1",
          wiped: true,
          objects_deleted: 4,
        });
      }),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    await userEvent.click(await screen.findByRole("button", { name: "Wipe Agentic AI" }));
    expect(await screen.findByRole("dialog", { name: "Wipe Agentic AI run?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Wipe" }));

    await waitFor(() => expect(requests).toEqual(["DELETE"]));
    expect(
      await screen.findByText("Agentic AI artifacts wiped. Docling extraction is unchanged."),
    ).toBeInTheDocument();
  });

  it("wipes extracted materials while retaining the selected book", async () => {
    const requests: string[] = [];
    serveLibrary([bookRow({ status: "done", n_pages: 1, pages_done: 1 })]);
    serveBook(snapshot({ status: "done", n_pages: 1, pages: [pageEntry(1)] }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "Extracted page" }),
      ),
      http.delete(`${API}/${BOOK_ID}/extraction`, ({ request }) => {
        requests.push(request.method);
        return HttpResponse.json({ book_id: BOOK_ID, wiped: true, objects_deleted: 8 });
      }),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    await userEvent.click(await screen.findByRole("button", { name: "Wipe Docling extraction" }));
    expect(await screen.findByRole("dialog", { name: "Wipe extracted materials?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Wipe" }));

    await waitFor(() => expect(requests).toEqual(["DELETE"]));
    expect(
      await screen.findByText("Extracted materials wiped. The uploaded book is preserved."),
    ).toBeInTheDocument();
  });

  it("cancels: asks the server, says it is stopping, then shows the cancelled state and offers Resume", async () => {
    const stream = liveStream();
    const requests: string[] = [];
    serveLibrary([bookRow()]);
    serveBook(snapshot({ status: "extracting" }), () => stream.response);
    server.use(
      http.post(`${API}/${BOOK_ID}/cancel`, () => {
        requests.push("cancel");
        return HttpResponse.json({ book_id: BOOK_ID, status: "cancelling" });
      }),
    );
    renderStage(BOOK_ID);
    await openPipeline();
    stream.send(startedEvent);
    stream.send(pageEvent(1));

    await userEvent.click(await screen.findByRole("button", { name: "Stop Docling extraction" }));
    expect(screen.getByRole("button", { name: "Stop Docling extraction" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop Docling extraction" })).toHaveTextContent("Stopping…");
    expect(requests).toEqual(["cancel"]);
    expect(await screen.findByText(/the run stops between slices/)).toBeInTheDocument();

    stream.send(terminalEvent("cancelled"));
    expect(await screen.findByRole("button", { name: "Resume Docling extraction" })).toBeEnabled();
    expect(screen.queryByText(/the run stops between slices/)).not.toBeInTheDocument();
  });

  it("shows why a run failed, and resumes it without uploading again", async () => {
    const runs: string[] = [];
    serveLibrary([bookRow()]);
    serveBook(snapshot({ status: "extracting" }), () =>
      sse([
        startedEvent,
        pageEvent(1),
        terminalEvent("failed", { error: "the converter ran out of memory on page 4" }),
      ]),
    );
    server.use(
      http.post(`${API}/${BOOK_ID}/run`, ({ request }) => {
        runs.push(new URL(request.url).search);
        return HttpResponse.json({ ...queued, resumed: true });
      }),
    );
    renderStage(BOOK_ID);

    await openPipeline();
    expect(await screen.findByRole("alert")).toHaveTextContent("the converter ran out of memory on page 4");
    await userEvent.click(screen.getByRole("button", { name: "Resume Docling extraction" }));
    await waitFor(() => expect(runs).toEqual([""]));
    expect(await screen.findByText(/Resuming the extraction where it stopped/)).toBeInTheDocument();
  });

  it("refreshes a finished book from its library dropdown option", async () => {
    const runs: string[] = [];
    serveLibrary([bookRow({ status: "done", pages_done: 9 })]);
    serveBook(snapshot({ status: "done", pages: [pageEntry(1)] }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/1`, () =>
        HttpResponse.json({ book_id: BOOK_ID, page: 1, markdown: "First page" }),
      ),
      http.post(`${API}/${BOOK_ID}/run`, ({ request }) => {
        runs.push(new URL(request.url).search);
        return HttpResponse.json(queued);
      }),
    );
    renderStage(BOOK_ID);

    await userEvent.click(screen.getByRole("checkbox", { name: "Run Agentic AI after extraction" }));
    await userEvent.click(await screen.findByRole("button", { name: BOOK_TITLE }));
    await userEvent.click(screen.getByRole("button", { name: `Refresh ${BOOK_TITLE}` }));
    await waitFor(() => expect(runs).toEqual(["?refresh=true&auto_chain=true"]));
    expect(push).toHaveBeenCalledWith(`/bd/ingestion?book=${BOOK_ID}`, { scroll: false });
    expect(await screen.findByText("Extraction queued.")).toBeInTheDocument();
  });

  it("reconnects when the stream drops, and the replay does not double anything", async () => {
    let connections = 0;
    serveLibrary([bookRow()]);
    serveBook(snapshot({ status: "extracting" }), () => {
      connections += 1;
      if (connections === 1) return new HttpResponse(null, { status: 503 });
      return sse([startedEvent, pageEvent(1), pageEvent(2)]);
    });
    renderStage(BOOK_ID);

    await openMaterials();
    await screen.findByRole("article", { name: "Page 2" });
    expect(connections).toBeGreaterThanOrEqual(2);
    expect(within(screen.getByRole("list", { name: "Extracted pages" })).getAllByRole("button")).toHaveLength(
      2,
    );
  });
});

describe("A finished book", () => {
  it("opens without a stream and reads a page from the server when it is chosen", async () => {
    const fetched: string[] = [];
    serveLibrary([bookRow({ status: "done", pages_done: 3 })]);
    // no events handler: connecting to one would fail the test (`onUnhandledFrame: "error"`)
    serveBook(snapshot({ status: "done", pages: [pageEntry(1), pageEntry(2), pageEntry(3)] }, null));
    server.use(
      http.get(`${API}/${BOOK_ID}/pages/:page`, ({ params }) => {
        fetched.push(String(params.page));
        return HttpResponse.json({
          book_id: BOOK_ID,
          page: Number(params.page),
          markdown:
            params.page === "3"
              ? "| Percentile | Latency |\n|---|---|\n| p99 | 480 ms |"
              : "# Intro\n\nIntro text\n\n## Methods\n\nBody.\n\n## Results\n\nEnd.",
        });
      }),
    );
    renderStage(BOOK_ID);

    await openMaterials();
    expect(await screen.findByText("Intro text")).toBeInTheDocument(); // the first page, not the last
    const outline = screen.getByRole("navigation", { name: "On this page" });
    expect(outline).toHaveTextContent("Methods");
    expect(outline).toHaveTextContent("Results");
    await userEvent.click(screen.getByRole("button", { name: "Hide outline" }));
    expect(screen.queryByRole("navigation", { name: "On this page" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show outline" }));
    expect(screen.queryByRole("button", { name: "Extract again" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: BOOK_TITLE }));
    expect(screen.getByRole("button", { name: `Refresh ${BOOK_TITLE}` })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: /^p\. 3\b/ }));
    expect(await screen.findByText("480 ms")).toBeInTheDocument();
    expect(fetched).toEqual(["1", "3"]);
  });

  it("says when the book cannot be opened", async () => {
    serveLibrary([]);
    server.use(
      http.get(`${API}/${BOOK_ID}`, () => HttpResponse.json({ detail: "book not found" }, { status: 404 })),
    );
    renderStage(BOOK_ID);

    expect(await screen.findByRole("alert")).toHaveTextContent("book not found");
  });

  describe("reader", () => {
    const reviewRun = () =>
      agenticRun({
        quality_status: "complete_with_review",
        expected_page_count: 3,
        published_page_count: 3,
        delivered_page_count: 2,
        discarded_blank_page_count: 1,
        review_page_count: 1,
        review_pages: [2],
        discarded_pages: [3],
      });

    function serveAgenticBook() {
      serveLibrary([bookRow({ status: "done", n_pages: 3, pages_done: 3 })]);
      serveBook(
        snapshot(
          { status: "done", n_pages: 3, pages: [pageEntry(1), pageEntry(2), pageEntry(3)] },
          null,
          reviewRun(),
        ),
      );
      server.use(
        http.get(`${API}/${BOOK_ID}/pages/:page`, ({ request, params }) => {
          const raw = new URL(request.url).searchParams.get("source") === "docling";
          const page = Number(params.page);
          return HttpResponse.json({
            book_id: BOOK_ID,
            page,
            markdown: raw ? `## I NTRODUC T I ON\n\nBody ${page}.` : `## INTRODUCTION\n\nBody ${page}.`,
            content_source: raw ? "docling" : "agentic",
          });
        }),
        http.get(`${API}/${BOOK_ID}/pages/2/quality`, () =>
          HttpResponse.json({
            book_id: BOOK_ID,
            agentic_run_id: "agentic-1",
            page: 2,
            page_kind: "text",
            status: "review_required",
            delivery_disposition: "include",
            issues: [
              {
                issue_id: "p0002-split_word",
                code: "split_word",
                severity: "medium",
                description: "An uppercase heading contains separated word fragments.",
                evidence: ["I NTRODUC T I ON"],
                resolution: "review",
                proposal: {
                  target_text: "I NTRODUC T I ON",
                  replacement: "INTRODUCTION",
                  rationale: "Join the fragments.",
                  confidence: 0.9,
                  risk: "low",
                },
              },
            ],
            mechanical_fixes: { control_characters: 1 },
            audit_warnings: [],
            errors: [],
          }),
        ),
        http.get(`${API}/${BOOK_ID}/agentic/runs/agentic-1/llm-counters`, () =>
          HttpResponse.json({
            total: { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 },
            by_node: {},
          }),
        ),
      );
    }

    it("compares the raw Docling page with the AI-processed one and shows what changed", async () => {
      serveAgenticBook();
      renderStage(BOOK_ID);

      await openMaterials();
      const reader = await screen.findByRole("article", { name: "Page 1" });
      expect(await within(reader).findByText("INTRODUCTION")).toBeInTheDocument();
      await userEvent.click(within(reader).getByRole("button", { name: "Docling raw" }));
      expect(await within(reader).findByText("I NTRODUC T I ON")).toBeInTheDocument();
      await userEvent.click(within(reader).getByRole("button", { name: "Changes" }));
      const changes = await within(reader).findByRole("region", { name: "Changes made by Agentic AI" });
      expect(changes).toHaveTextContent("2 changed lines");
      expect(within(changes).getByText("Removed:", { exact: false })).toBeInTheDocument();
      expect(within(changes).getByText("Added:", { exact: false })).toBeInTheDocument();
    });

    it("shows what the Agentic AI step found on a page, with the fix it would suggest", async () => {
      serveAgenticBook();
      renderStage(BOOK_ID);

      await openMaterials();
      await userEvent.click(await screen.findByRole("button", { name: /^p\. 2\b/ }));
      const quality = await screen.findByRole("region", { name: "Page quality" });
      expect(quality).toHaveTextContent("Needs review");
      expect(quality).toHaveTextContent("Removed 1 control character automatically.");
      const issues = within(quality).getByRole("list", { name: "Issues found" });
      expect(issues).toHaveTextContent("Split word");
      expect(issues).toHaveTextContent("Needs review");
      expect(issues).toHaveTextContent("Suggested fix: I NTRODUC T I ON → replaced by INTRODUCTION");
    });

    it("marks the pages the run left for review or out of the text, filters to review, and jumps to the next one", async () => {
      serveAgenticBook();
      renderStage(BOOK_ID);

      await openMaterials();
      const list = await screen.findByRole("list", { name: "Extracted pages" });
      expect(within(list).getByRole("button", { name: /^p\. 2\b/ })).toHaveTextContent("Needs review");
      expect(within(list).getByRole("button", { name: /^p\. 3\b/ })).toHaveTextContent(
        "Blank · not delivered",
      );
      await userEvent.click(screen.getByRole("checkbox", { name: /Only pages that need review · 1/ }));
      expect(within(list).getAllByRole("button")).toHaveLength(1);
      await userEvent.click(screen.getByRole("button", { name: /^Next to review/ }));
      expect(await screen.findByRole("article", { name: "Page 2" })).toBeInTheDocument();
    });

    it("remembers which side columns are folded and the page the book was left on", async () => {
      serveAgenticBook();
      const first = renderStage(BOOK_ID);

      await openMaterials();
      await userEvent.click(await screen.findByRole("button", { name: /^p\. 3\b/ }));
      await userEvent.click(screen.getByRole("button", { name: "Hide contents" }));
      expect(screen.queryByRole("list", { name: "Extracted pages" })).not.toBeInTheDocument();
      expect(JSON.parse(window.localStorage.getItem(READER_LAYOUT_STORAGE_KEY) ?? "{}")).toMatchObject({
        contents: false,
      });
      first.unmount();

      renderStage(BOOK_ID);
      await openMaterials();
      expect(await screen.findByRole("article", { name: "Page 3" })).toBeInTheDocument();
      expect(screen.queryByRole("list", { name: "Extracted pages" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Show contents" })).toBeInTheDocument();
    });

    it("keeps the reading window's text width across openings", async () => {
      serveAgenticBook();
      renderStage(BOOK_ID);

      await openMaterials();
      await userEvent.click(await screen.findByRole("button", { name: "Reading mode" }));
      const dialog = await screen.findByRole("dialog", { name: BOOK_TITLE });
      await userEvent.click(within(dialog).getByRole("button", { name: /Wide/ }));
      expect(JSON.parse(window.localStorage.getItem(READER_LAYOUT_STORAGE_KEY) ?? "{}")).toMatchObject({
        width: "wide",
      });
      expect(within(dialog).getByRole("button", { name: /Wide/ })).toHaveAttribute("aria-pressed", "true");
    });
  });

  it("shows a queued book as waiting, with Stop, and a book with no outline plainly", async () => {
    serveLibrary([bookRow({ status: "queued", pages_done: 0 })]);
    serveBook(
      snapshot({ status: "queued", outline: [], chapters: [] }, { status: "queued" }),
      () => liveStream().response,
    );
    renderStage(BOOK_ID);

    await openMaterials();
    expect(await screen.findByText(/Queued — waiting for a worker/)).toBeInTheDocument();
    expect(screen.getByText("This book has no outline.")).toBeInTheDocument();
    expect(screen.getByText("Waiting for the first page…")).toBeInTheDocument();
    await openPipeline();
    expect(
      within(screen.getByRole("region", { name: "Docling extraction progress" })).getByRole("button", {
        name: "Stop Docling extraction",
      }),
    ).toBeInTheDocument();
  });
});
