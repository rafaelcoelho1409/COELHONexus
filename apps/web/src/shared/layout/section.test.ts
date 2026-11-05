import { describe, expect, it } from "vitest";

import { currentTabKey, type Section, sectionFor, tabHref, visibleTabs } from "./section";

const DOCS: Section = {
  href: "/dd",
  title: "Docs Distiller",
  description: "d",
  defaultTab: "catalog",
  carryParam: "slug",
  tabs: [
    { key: "catalog", label: "Catalog", href: "/dd", tab: true, carry: false },
    { key: "ingestion", label: "Ingestion", href: "/dd/ingestion", tab: true, carry: true },
    { key: "planner", label: "Planner", href: "/dd/planner", tab: false, carry: true },
  ],
};
const BOOKS: Section = {
  href: "/bd",
  title: "Book Distiller",
  description: "d",
  defaultTab: "ingestion",
  tabs: [{ key: "ingestion", label: "Ingestion", href: "/bd/ingestion", tab: true, carry: false }],
};
const SETTINGS: Section = { href: "/settings", title: "Settings", description: "d", tabs: [] };
const SECTIONS = [DOCS, BOOKS, SETTINGS];

describe("sectionFor", () => {
  it("finds the section a path is in: its own path and every path below it", () => {
    expect(sectionFor(SECTIONS, "/dd")).toBe(DOCS);
    expect(sectionFor(SECTIONS, "/dd/planner")).toBe(DOCS);
    expect(sectionFor(SECTIONS, "/settings")).toBe(SETTINGS);
  });

  it("does not mistake a longer name for a child path, nor invent a section for the home page", () => {
    expect(sectionFor(SECTIONS, "/ddx")).toBeNull();
    expect(sectionFor(SECTIONS, "/")).toBeNull();
  });
});

describe("currentTabKey", () => {
  it("is the tab whose path is exact", () => {
    expect(currentTabKey(DOCS, "/dd/ingestion")).toBe("ingestion");
    expect(currentTabKey(DOCS, "/dd")).toBe("catalog");
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
    expect(visibleTabs(DOCS, "/dd/ingestion").map((tab) => tab.key)).toEqual(["catalog", "ingestion"]);
    expect(visibleTabs(DOCS, "/dd/planner").map((tab) => tab.key)).toEqual([
      "catalog",
      "ingestion",
      "planner",
    ]);
  });
});

function tabOf(section: Section, key: string) {
  const found = section.tabs.find((tab) => tab.key === key);
  if (!found) throw new Error(`no tab ${key}`);
  return found;
}

describe("tabHref", () => {
  it("carries the framework to a stage that takes it, encoded", () => {
    expect(tabHref(DOCS, tabOf(DOCS, "ingestion"), "next.js/app")).toBe("/dd/ingestion?slug=next.js%2Fapp");
  });

  it("never carries it to the catalog, nor when there is none", () => {
    expect(tabHref(DOCS, tabOf(DOCS, "catalog"), "a")).toBe("/dd");
    expect(tabHref(DOCS, tabOf(DOCS, "ingestion"), null)).toBe("/dd/ingestion");
  });

  it("carries nothing in a section that names no parameter", () => {
    expect(tabHref(BOOKS, tabOf(BOOKS, "ingestion"), "a")).toBe("/bd/ingestion");
  });
});
