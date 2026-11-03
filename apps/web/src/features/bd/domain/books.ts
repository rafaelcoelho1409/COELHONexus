import type { BookRow } from "@/shared/api";

import { MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL } from "../params";

export type BookStatus = "queued" | "extracting" | "done" | "failed" | "cancelled" | "unknown";
export type BookUnits = { singular: "page" | "section"; plural: "pages" | "sections" };

const PDF_UNITS: BookUnits = { singular: "page", plural: "pages" };
const EPUB_UNITS: BookUnits = { singular: "section", plural: "sections" };

export function bookUnits(filename: string): BookUnits {
  return /\.epub$/i.test(filename) ? EPUB_UNITS : PDF_UNITS;
}

/** The server's manifest status (`queued|extracting|done|failed|cancelled`) and its run progress status
 * (`queued|running|…`) folded into one vocabulary. Anything unrecognised is shown as such, never as a guess. */
export function toBookStatus(raw: string | null | undefined): BookStatus {
  switch (raw) {
    case "queued":
    case "done":
    case "failed":
    case "cancelled":
    case "extracting":
      return raw;
    case "running":
    case "cancelling":
      return "extracting";
    default:
      return "unknown";
  }
}

export const STATUS_LABEL: Record<BookStatus, string> = {
  queued: "queued",
  extracting: "extracting",
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
  unknown: "unknown",
};

/** A run is in flight (or about to be): there is something to follow and to cancel. */
export function isLive(status: BookStatus): boolean {
  return status === "queued" || status === "extracting";
}

/** The library, newest upload first. */
export function newestFirst(rows: readonly BookRow[]): BookRow[] {
  return [...rows].sort((a, b) => b.created_at - a.created_at);
}

export function libraryHasLiveBook(rows: readonly BookRow[] | undefined): boolean {
  return rows?.some((row) => isLive(toBookStatus(row.status))) ?? false;
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

/** Why a file cannot be sent, or null. The server checks the content too; this only saves a round trip. */
export function bookFileProblem(file: { name: string; size: number; type: string }): string | null {
  const extension = /\.pdf$/i.test(file.name) ? "pdf" : /\.epub$/i.test(file.name) ? "epub" : null;
  const mediaType =
    file.type === "application/pdf" ? "pdf" : file.type === "application/epub+zip" ? "epub" : null;
  if (!extension && !mediaType) return "Only PDF or EPUB files can be uploaded.";
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) return `That file is over ${MAX_UPLOAD_LABEL} (${fmtBytes(file.size)}).`;
  return null;
}

/** 0–100, or null when the total is not known yet. */
export function percent(done: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}
