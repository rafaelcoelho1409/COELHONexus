/** Fixtures that look like real data (long titles, nested section paths, a table, an untagged code fence), shared by
 * the unit tests. The e2e spec carries its own copy, as the DD one does. */

import { HttpResponse } from "msw";

import type { BookRow, Manifest, PageEntry, Progress } from "@/shared/api";
import type { AgenticProgress, AgenticRunStatus, BookSnapshot } from "./entities";

export const BOOK_ID = "9f8e7d6c5b4a3210";
export const BOOK_TITLE =
  "Designing Data-Intensive Applications: The Big Ideas Behind Reliable, Scalable Systems";

export const OUTLINE = [
  { level: 1, title: "Foundations of Data Systems", page: 1 },
  { level: 2, title: "Reliability", page: 2 },
  { level: 2, title: "Scalability", page: 4 },
  { level: 1, title: "Distributed Data", page: 6 },
] as const;

export const CHAPTERS = [
  { title: "Foundations of Data Systems", first_page: 1, last_page: 5 },
  { title: "Distributed Data", first_page: 6, last_page: 9 },
] as const;

export function bookRow(over: Partial<BookRow> = {}): BookRow {
  return {
    book_id: BOOK_ID,
    title: BOOK_TITLE,
    filename: "designing-data-intensive-applications-2nd-edition-final.pdf",
    size_bytes: 18_874_368,
    n_pages: 9,
    pages_done: 3,
    status: "extracting",
    created_at: 1_793_000_000,
    updated_at: 1_793_000_100,
    ...over,
  };
}

export function pageEntry(page: number, over: Partial<PageEntry> = {}): PageEntry {
  return {
    page,
    key: `pages/${String(page).padStart(4, "0")}.md`,
    chars: 2400,
    headings: page === 1 ? 1 : 0,
    tables: page === 3 ? 1 : 0,
    pictures: page === 2 ? 1 : 0,
    formulas: 0,
    figures: page === 2 ? ["p0002-1.png"] : [],
    section_path:
      page < 4
        ? ["Foundations of Data Systems", page < 3 ? "Reliability" : "Scalability"]
        : ["Distributed Data"],
    ...over,
  };
}

function manifest(over: Partial<Manifest> = {}): Manifest {
  return {
    book_id: BOOK_ID,
    title: BOOK_TITLE,
    filename: "designing-data-intensive-applications-2nd-edition-final.pdf",
    size_bytes: 18_874_368,
    n_pages: 9,
    status: "extracting",
    error: null,
    outline: OUTLINE.map((entry) => ({ ...entry })),
    chapters: CHAPTERS.map((chapter) => ({ ...chapter })),
    pages: [],
    created_at: 1_793_000_000,
    updated_at: 1_793_000_100,
    ...over,
  };
}

function progress(over: Partial<Progress> = {}): Progress {
  return {
    run_id: "run-1",
    status: "running",
    completed: 0,
    total: 9,
    error: null,
    started_at: 1_793_000_000,
    updated_at: 1_793_000_100,
    ...over,
  };
}

export function snapshot(
  manifestOver: Partial<Manifest> = {},
  progressOver: Partial<Progress> | null = {},
  agentic: AgenticRunStatus | null = null,
): BookSnapshot {
  return {
    manifest: manifest(manifestOver),
    progress: progressOver === null ? null : progress(progressOver),
    agentic,
  };
}

export function agenticRun(over: Partial<AgenticRunStatus> = {}): AgenticRunStatus {
  const progress: AgenticProgress = {
    current_node: null,
    node_status: {},
    audited_pages: [],
    audit_warning_pages: [],
    processed_pages: [],
    active_audit_pages: [],
    active_repair_pages: [],
  };
  return {
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
    progress,
    created_at: 1,
    updated_at: 2,
    finished_at: 2,
    error: null,
    ...over,
  };
}

const PAGE_TEXT: Record<number, string> = {
  1: "# Foundations of Data Systems\n\nThe Internet was done so well that most people think of it as a natural resource.\n\n```\nGET /v1/orders?limit=20\n```",
  2: "## Reliability\n\nContinuing to work correctly, even when things go wrong.",
  3: "## Scalability\n\n| Percentile | Latency |\n|---|---|\n| p50 | 12 ms |\n| p99 | 480 ms |",
};

export const startedEvent = {
  kind: "started",
  ts: 1,
  title: BOOK_TITLE,
  n_pages: 9,
  outline: OUTLINE,
  chapters: CHAPTERS,
  resumed: false,
  completed: 0,
} as const;

export function pageEvent(page: number, extra: Record<string, unknown> = {}) {
  const entry = pageEntry(page);
  return {
    kind: "page",
    ts: page + 1,
    page,
    markdown: PAGE_TEXT[page] ?? `Page ${page} body text.`,
    truncated: false,
    stats: {
      chars: entry.chars,
      headings: entry.headings,
      tables: entry.tables,
      pictures: entry.pictures,
      formulas: entry.formulas,
    },
    figures: entry.figures,
    section_path: entry.section_path,
    completed: page,
    total: 9,
    ...extra,
  };
}

export const sliceDone = { kind: "slice_done", ts: 9, first: 1, last: 3, seconds: 12 } as const;

export function terminalEvent(kind: "done" | "failed" | "cancelled", extra: Record<string, unknown> = {}) {
  return { kind, ts: 20, pages: kind === "done" ? 9 : 3, total: 9, error: null, ...extra };
}

/** A finished stream, as the server's replay sends it. */
export function sse(events: readonly unknown[]): HttpResponse<string> {
  const body = `${events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")}\n\n`;
  return new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });
}
