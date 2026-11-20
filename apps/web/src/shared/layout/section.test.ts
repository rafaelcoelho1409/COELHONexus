import { describe, expect, it } from "vitest";

import { currentTabKey, type Section, sectionFor, tabHref, visibleTabs } from "./section";

const BOOKS: Section = {
  href: "/bd",
  title: "Book Distiller",
  description: "d",
  defaultTab: "ingestion",
  carryParam: "book",
  tabs: [
    { key: "ingestion", label: "Ingestion", href: "/bd/ingestion", tab: true, carry: true },
    { key: "pages", label: "Pages", href: "/bd/pages", tab: true, carry: true },
    { key: "distill", label: "Distill", href: "/bd/distill", tab: true, carry: true },
    { key: "study", label: "Study", href: "/bd/study", tab: true, carry: true },
    { key: "report", label: "Report", href: "/bd/report", tab: false, carry: false },
  ],
};
const SETTINGS: Section = { href: "/settings", title: "Settings", description: "d", tabs: [] };
const SECTIONS = [BOOKS, SETTINGS];

describe("sectionFor", () => {
  it("finds the section a path is in: its own path and every path below it", () => {
    expect(sectionFor(SECTIONS, "/bd")).toBe(BOOKS);
    expect(sectionFor(SECTIONS, "/bd/study")).toBe(BOOKS);
    expect(sectionFor(SECTIONS, "/settings")).toBe(SETTINGS);
  });

  it("does not mistake a longer name for a child path, nor invent a section for the home page", () => {
    expect(sectionFor(SECTIONS, "/bdx")).toBeNull();
    expect(sectionFor(SECTIONS, "/")).toBeNull();
  });
});

describe("currentTabKey", () => {
  it("is the tab whose path is exact", () => {
    expect(currentTabKey(BOOKS, "/bd/pages")).toBe("pages");
    expect(currentTabKey(BOOKS, "/bd")).toBe("ingestion");
  });

  it("falls back to the section's default tab on a path that is none of them", () => {
    expect(currentTabKey(BOOKS, "/bd")).toBe("ingestion");
  });

  it("is nothing for a section without tabs", () => {
    expect(currentTabKey(SETTINGS, "/settings")).toBeNull();
  });
});

describe("visibleTabs", () => {
  it("leaves a deep link out of the strip until the reader is on it", () => {
    expect(visibleTabs(BOOKS, "/bd/study").map((tab) => tab.key)).toEqual([
      "ingestion",
      "pages",
      "distill",
      "study",
    ]);
    expect(visibleTabs(BOOKS, "/bd/report").map((tab) => tab.key)).toContain("report");
  });
});

function tabOf(section: Section, key: string) {
  const found = section.tabs.find((tab) => tab.key === key);
  if (!found) throw new Error(`no tab ${key}`);
  return found;
}

describe("tabHref", () => {
  it("carries the book identifier to a stage that takes it, encoded", () => {
    expect(tabHref(BOOKS, tabOf(BOOKS, "pages"), "next.js/app")).toBe("/bd/pages?book=next.js%2Fapp");
  });

  it("never carries it to a destination that does not accept it", () => {
    expect(tabHref(BOOKS, tabOf(BOOKS, "report"), "a")).toBe("/bd/report");
    expect(tabHref(BOOKS, tabOf(BOOKS, "pages"), null)).toBe("/bd/pages");
  });

  it("carries nothing in a section that names no parameter", () => {
    const sourceTab = { key: "source", label: "Source", href: "/ycs/source", tab: true, carry: true };
    const ycs: Section = { href: "/ycs", title: "YCS", description: "d", tabs: [sourceTab] };
    expect(tabHref(ycs, sourceTab, "a")).toBe("/ycs/source");
  });
});
