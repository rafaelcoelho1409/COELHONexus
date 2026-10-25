import { describe, expect, it } from "vitest";

import { type ManifestEntry, manifestSchema, runStatusSchema } from "../entities";
import {
  entryBasename,
  explorerTierValues,
  filterEntries,
  findEntryByBasename,
  groupEntries,
  ingressSummary,
  isRunTerminal,
  manifestView,
  ROOT_GROUP,
  runProgressView,
  tierLabel,
} from "./ingestion";

describe("ingestion run progress", () => {
  it("maps tier keys to labels and keeps an unknown key verbatim", () => {
    expect(tierLabel("llms_full")).toBe("Fetching documentation bundle");
    expect(tierLabel("post")).toBe("Splitting & deduplicating pages");
    expect(tierLabel("weird")).toBe("weird");
    expect(tierLabel(null)).toBe("—");
  });

  it("computes a determinate percent and counter", () => {
    const status = runStatusSchema.parse({
      progress: { tier: "docs", status: "running", current: 3, total: 10, last_url: "u" },
      post: null,
    });
    expect(runProgressView(status.progress, null)).toMatchObject({
      status: "running",
      percent: 30,
      counter: "3 / 10 (30%)",
    });
  });

  it("falls back to an indeterminate counter without a total", () => {
    const status = runStatusSchema.parse({ progress: { status: "running", current: 4 } });
    const view = runProgressView(status.progress, null);
    expect(view.percent).toBeNull();
    expect(view.counter).toBe("4 so far…");
  });

  it("shows the split summary even when progress is absent", () => {
    const status = runStatusSchema.parse({
      progress: null,
      post: { was_split: true, input_files: 5, output_files: 40, duplicates_dropped: 2, stubs_dropped: 1 },
    });
    expect(runProgressView(status.progress, status.post).postSummary).toBe(
      "(split into 40 pages, -2 dupes, -1 stubs)",
    );
  });

  it("treats done, failed and cancelled as terminal", () => {
    expect(isRunTerminal("done")).toBe(true);
    expect(isRunTerminal("failed")).toBe(true);
    expect(isRunTerminal("cancelling")).toBe(false);
    expect(isRunTerminal(null)).toBe(false);
  });
});

function entry(extra: Partial<ManifestEntry>): ManifestEntry {
  return {
    idx: 0,
    key: null,
    title: null,
    slug: null,
    tier: null,
    bytes: null,
    url: null,
    source_path: null,
    section: null,
    notes: null,
    ...extra,
  };
}

describe("citation file names", () => {
  const pages = [
    entry({ idx: 0, key: "ingest/alpha/pages/0000-intro.md", slug: "intro" }),
    entry({ idx: 12, slug: "setup" }), // a manifest written before keys were stored
  ];

  it("names a page by the last segment of its key, or rebuilds the name without one", () => {
    expect(entryBasename(pages[0] as ManifestEntry)).toBe("0000-intro.md");
    expect(entryBasename(pages[1] as ManifestEntry)).toBe("0012-setup.md");
    expect(entryBasename(entry({ idx: 3 }))).toBe("0003-page.md");
  });

  it("finds the page a citation names, forgiving case and a missing or extra .md", () => {
    expect(findEntryByBasename(pages, "0000-intro.md")).toBe(0);
    expect(findEntryByBasename(pages, " 0000-INTRO ")).toBe(0);
    expect(findEntryByBasename(pages, "0012-setup")).toBe(1);
    expect(findEntryByBasename(pages, "0099-missing.md")).toBe(-1);
    expect(findEntryByBasename(pages, "")).toBe(-1);
  });
});

describe("manifest view", () => {
  const entries = [
    entry({
      idx: 0,
      title: "Quickstart",
      tier: "llms-full",
      section: "Docs",
      url: "https://docs.example.com/docs/quickstart",
    }),
    entry({ idx: 1, title: "CLI", section: "Docs", url: "https://example.com/docs/some/cli" }),
    entry({ idx: 2, title: "Vendor", url: "https://example.com/v5.9.0/notes" }),
  ];

  it("summarises the manifest", () => {
    const manifest = manifestSchema.parse({
      framework_name: "Example",
      entries,
      total_bytes: 320,
      tier_kind: "sitemap",
      ingested_at: Math.floor(Date.now() / 1000) - 120,
    });
    expect(manifestView(manifest)).toMatchObject({ pageCount: 3, framework: "Example" });
    expect(ingressSummary(manifest)).toContain("Example · 3 pages · 320 B sitemap · 2m ago");
    expect(ingressSummary(null)).toBe("");
  });

  it("groups by curated section when present, root first", () => {
    const groups = groupEntries(entries);
    expect(groups.map((g) => g.prefix)).toEqual([ROOT_GROUP, "Docs"]);
    expect(groups[0]?.items[0]?.index).toBe(2);
  });

  it("skips version pins and scope roots but keeps a genuine python pair", () => {
    const groups = groupEntries([
      entry({ idx: 0, url: "https://x.com/v5.9.0/guides/setup" }),
      entry({ idx: 1, url: "https://x.com/python/commands" }),
      entry({ idx: 2, url: "https://x.com/usage/commands/config" }),
      entry({ idx: 3, url: "https://x.com" }),
    ]);
    expect(groups.map((g) => g.prefix)).toEqual([ROOT_GROUP, "guides", "python", "usage/commands"]);
  });

  it("filters entries by search text and tier, and lists the tiers present", () => {
    expect(filterEntries(entries, new Set(), "Cli")).toEqual([1]);
    expect(filterEntries(entries, new Set(["llms-full"]), "")).toEqual([0]);
    expect(explorerTierValues(entries)).toEqual(["llms-full"]);
  });
});
