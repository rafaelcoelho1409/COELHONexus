import { describe, expect, it } from "vitest";

import { bookRow } from "../test-fixtures";
import {
  bookFileProblem,
  bookFormat,
  bookUnits,
  fmtBytes,
  isLive,
  libraryHasLiveBook,
  newestFirst,
  percent,
  toBookStatus,
} from "./books";

describe("toBookStatus", () => {
  it("folds the manifest and run vocabularies into one", () => {
    expect(toBookStatus("extracting")).toBe("extracting");
    expect(toBookStatus("running")).toBe("extracting");
    expect(toBookStatus("cancelling")).toBe("extracting");
    expect(toBookStatus("queued")).toBe("queued");
    expect(toBookStatus("done")).toBe("done");
  });

  it("does not guess at a status it does not know", () => {
    expect(toBookStatus("exploded")).toBe("unknown");
    expect(toBookStatus(null)).toBe("unknown");
    expect(toBookStatus(undefined)).toBe("unknown");
  });

  it("calls only queued and extracting live", () => {
    expect(
      ["queued", "extracting", "done", "failed", "cancelled", "unknown"].map((s) => isLive(toBookStatus(s))),
    ).toEqual([true, true, false, false, false, false]);
  });
});

describe("library", () => {
  it("lists the newest upload first without touching the input", () => {
    const rows = [bookRow({ book_id: "old", created_at: 1 }), bookRow({ book_id: "new", created_at: 9 })];
    expect(newestFirst(rows).map((row) => row.book_id)).toEqual(["new", "old"]);
    expect(rows.map((row) => row.book_id)).toEqual(["old", "new"]);
  });

  it("polls only while a book is being extracted", () => {
    expect(libraryHasLiveBook(undefined)).toBe(false);
    expect(libraryHasLiveBook([bookRow({ status: "done" })])).toBe(false);
    expect(libraryHasLiveBook([bookRow({ status: "done" }), bookRow({ status: "queued" })])).toBe(true);
  });
});

describe("bookFileProblem", () => {
  const pdf = { name: "Book.PDF", size: 1_000, type: "" };
  const epub = { name: "Book.EPUB", size: 1_000, type: "application/octet-stream" };

  it("accepts PDF and EPUB by extension or media type", () => {
    expect(bookFileProblem(pdf)).toBeNull();
    expect(bookFileProblem(epub)).toBeNull();
    expect(bookFileProblem({ name: "scan", size: 1_000, type: "application/pdf" })).toBeNull();
    expect(bookFileProblem({ name: "ebook", size: 1_000, type: "application/epub+zip" })).toBeNull();
  });

  it("refuses other files, empty files and files over 300 MB before anything is sent", () => {
    expect(bookFileProblem({ name: "notes.txt", size: 1_000, type: "text/plain" })).toMatch(
      /Only PDF or EPUB/,
    );
    expect(bookFileProblem({ name: "misnamed.epub", size: 1_000, type: "application/pdf" })).toBeNull();
    expect(bookFileProblem({ ...pdf, size: 0 })).toMatch(/empty/);
    expect(bookFileProblem({ ...pdf, size: 300 * 1024 * 1024 })).toBeNull();
    expect(bookFileProblem({ ...pdf, size: 300 * 1024 * 1024 + 1 })).toMatch(/over 300 MB/);
  });
});

describe("bookUnits", () => {
  it("uses sections for EPUB and preserves page labels for older PDF books", () => {
    expect(bookUnits("novel.EPUB")).toEqual({ singular: "section", plural: "sections" });
    expect(bookUnits("scan.pdf")).toEqual({ singular: "page", plural: "pages" });
    expect(bookUnits("older-book")).toEqual({ singular: "page", plural: "pages" });
  });
});

describe("bookFormat", () => {
  it("shows supported file formats without depending on filename case", () => {
    expect(bookFormat("guide.PDF")).toBe("PDF");
    expect(bookFormat("novel.epub")).toBe("EPUB");
    expect(bookFormat("unknown.docx")).toBe("Unknown");
  });
});

describe("formatting", () => {
  it("writes sizes in the unit a person reads", () => {
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(1536)).toBe("1.5 KB");
    expect(fmtBytes(18_874_368)).toBe("18 MB");
    expect(fmtBytes(5 * 1024 ** 3)).toBe("5.0 GB");
  });

  it("clamps a percentage and says nothing when the total is unknown", () => {
    expect(percent(3, 9)).toBe(33);
    expect(percent(12, 9)).toBe(100);
    expect(percent(0, 0)).toBeNull();
  });
});
