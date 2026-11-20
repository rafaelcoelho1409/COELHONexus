import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Section } from "./section";
import { SectionBar } from "./section-bar";

const nav = vi.hoisted(() => ({ pathname: "/bd", search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const SECTIONS: Section[] = [
  {
    href: "/bd",
    title: "Book Distiller",
    description: "Upload, distill, study, ask, and inspect book knowledge",
    defaultTab: "ingestion",
    carryParam: "book",
    tabs: [
      { key: "ingestion", label: "Ingestion", href: "/bd/ingestion", tab: true, carry: true },
      { key: "pages", label: "Pages", href: "/bd/pages", tab: true, carry: true },
      { key: "distill", label: "Distill", href: "/bd/distill", tab: true, carry: true },
      { key: "study", label: "Study", href: "/bd/study", tab: true, carry: true },
      { key: "report", label: "Report", href: "/bd/report", tab: false, carry: false },
    ],
  },
  { href: "/settings", title: "Settings", description: "The connections", tabs: [] },
];

function renderAt(pathname: string, search = "") {
  nav.pathname = pathname;
  nav.search = search;
  return render(<SectionBar sections={SECTIONS} />);
}

describe("SectionBar", () => {
  it("shows the page title as the one heading, with its description", () => {
    renderAt("/bd");
    expect(screen.getByRole("heading", { level: 1, name: "Book Distiller" })).toBeInTheDocument();
    expect(screen.getByText("Upload, distill, study, ask, and inspect book knowledge")).toBeInTheDocument();
  });

  it("lists the stage tabs, and marks the current one with aria-current", () => {
    renderAt("/bd/ingestion");
    const tabs = within(screen.getByRole("navigation", { name: "Book Distiller stages" }));
    expect(tabs.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Ingestion",
      "Pages",
      "Distill",
      "Study",
    ]);
    expect(tabs.getByRole("link", { name: "Ingestion" })).toHaveAttribute("aria-current", "page");
    expect(tabs.getByRole("link", { name: "Pages" })).not.toHaveAttribute("aria-current");
  });

  it("keeps the book identifier on tabs that accept it", () => {
    renderAt("/bd/pages", "book=next-js");
    expect(screen.getByRole("link", { name: "Pages" })).toHaveAttribute(
      "href",
      "/bd/pages?book=next-js",
    );
    expect(screen.getByRole("link", { name: "Ingestion" })).toHaveAttribute(
      "href",
      "/bd/ingestion?book=next-js",
    );
  });

  it("shows a deep-link stage only while the reader is on it", () => {
    renderAt("/bd/report");
    expect(screen.getByRole("link", { name: "Report" })).toHaveAttribute("aria-current", "page");
  });

  it("has no tab strip for a section without stages", () => {
    renderAt("/settings");
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("renders nothing outside the console sections", () => {
    const { container } = renderAt("/");
    expect(container).toBeEmptyDOMElement();
  });
});
