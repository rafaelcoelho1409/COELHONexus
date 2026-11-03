import { describe, expect, it } from "vitest";

import { bdEventSchema } from "../entities";
import { pageEntry, pageEvent } from "../test-fixtures";
import { breadcrumb, mergePages, type PageRow, pageChips, readableMarkdown, shownPage } from "./pages";
import { EMPTY_RUN, runReducer } from "./run-state";

describe("mergePages", () => {
  it("lists the manifest's pages and the stream's together, ascending, the stream winning", () => {
    const live = runReducer(EMPTY_RUN, {
      type: "event",
      event: bdEventSchema.parse(pageEvent(3, { section_path: ["Fresh"] })),
    });
    const rows = mergePages([pageEntry(2), pageEntry(1), pageEntry(3)], live.pages);
    expect(rows.map((row) => row.page)).toEqual([1, 2, 3]);
    expect(rows[2]?.sectionPath).toEqual(["Fresh"]);
  });

  it("lists a streamed page the manifest does not have yet", () => {
    const live = runReducer(EMPTY_RUN, { type: "event", event: bdEventSchema.parse(pageEvent(4)) });
    expect(mergePages([pageEntry(1)], live.pages).map((row) => row.page)).toEqual([1, 4]);
  });
});

describe("page labels", () => {
  it("writes only the nonzero counts, in words", () => {
    expect(pageChips({ headings: 1, tables: 2, pictures: 0, formulas: 3 })).toEqual([
      "1 heading",
      "2 tables",
      "3 formulas",
    ]);
    expect(pageChips({ headings: 0, tables: 0, pictures: 0, formulas: 0 })).toEqual([]);
  });

  it("joins the section path, and is empty before the first heading", () => {
    expect(breadcrumb(["Part I", "Chapter 2", "Limits"])).toBe("Part I › Chapter 2 › Limits");
    expect(breadcrumb([])).toBe("");
  });
});

describe("readableMarkdown", () => {
  it("drops the converter's picture placeholders and the blank lines they leave", () => {
    expect(readableMarkdown("<!-- image -->\n\n\n# Title\n\nText <!-- note\nover lines --> more")).toBe(
      "# Title\n\nText  more",
    );
  });

  it("leaves ordinary markdown alone", () => {
    expect(readableMarkdown("## Heading\n\n- one\n- two")).toBe("## Heading\n\n- one\n- two");
  });
});

describe("shownPage", () => {
  const rows: PageRow[] = [1, 2, 3].map((page) => ({
    page,
    headings: 0,
    tables: 0,
    pictures: 0,
    formulas: 0,
    figures: [],
    sectionPath: [],
  }));

  it("follows the newest page while following", () => {
    expect(shownPage({ follow: true, picked: 1 }, 3, rows)).toBe(3);
    expect(shownPage({ follow: true, picked: null }, null, rows)).toBe(3);
  });

  it("shows the page picked, else the first, when not following", () => {
    expect(shownPage({ follow: false, picked: 2 }, 3, rows)).toBe(2);
    expect(shownPage({ follow: false, picked: null }, 3, rows)).toBe(1);
    expect(shownPage({ follow: false, picked: null }, null, [])).toBeNull();
  });
});
