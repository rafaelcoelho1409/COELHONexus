import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { manifestSchema } from "../entities";
import { Explorer } from "./explorer";

const ORIGIN = "http://nexus.test";
const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(`${ORIGIN}/api/v1/dd/ingestion/demo/pages/*`, () =>
      HttpResponse.json({ body: "# Page\n\ntext" }),
    ),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function entry(idx: number, title: string, slug: string) {
  return {
    idx,
    key: null,
    title,
    slug,
    tier: "llms_full",
    bytes: 1_000,
    url: "https://docs.example.com/llms-full.txt",
    source_path: `https://docs.example.com/docs/${slug}`,
    section: "Docs",
    notes: null,
  };
}

function renderExplorer(entries: ReturnType<typeof entry>[]) {
  const manifest = manifestSchema.parse({
    framework_name: "Demo",
    framework_slug: "demo",
    entries,
    total_bytes: 3_000,
    tier_kind: "llms_full",
    ingested_at: 1,
  });
  return render(
    <QueryProvider>
      <Explorer slug="demo" manifest={manifest} />
    </QueryProvider>,
  );
}

function listedTitles() {
  const tree = screen.getByRole("region", { name: "Ingested library" });
  return within(tree)
    .getAllByRole("button")
    .map((button) => button.textContent ?? "")
    .filter((text) => /Zebra|Apple|Mango/.test(text));
}

describe("Explorer sidebar", () => {
  const entries = [
    entry(0, "Zebra crossing", "demo-llms-full-zebra-crossing"),
    entry(1, "Apple pie", "demo-llms-full-apple-pie"),
    entry(2, "Mango › Chutney", "demo-llms-full-mango-chutney"),
  ];

  it("lists each page by its title, not by its slug", () => {
    renderExplorer(entries);
    const texts = listedTitles().join(" | ");
    expect(texts).toContain("Zebra crossing");
    expect(texts).toContain("Mango › Chutney");
    expect(texts).not.toContain("demo-llms-full");
  });

  it("keeps the original documentation order instead of sorting by name", () => {
    renderExplorer(entries);
    expect(listedTitles().map((text) => text.split(" · ")[0]?.trim())).toEqual([
      expect.stringContaining("Zebra crossing"),
      expect.stringContaining("Apple pie"),
      expect.stringContaining("Mango › Chutney"),
    ]);
  });
});
