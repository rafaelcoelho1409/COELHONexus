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
  runFramework,
  runProgressView,
  runToAdopt,
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

describe("runFramework", () => {
  const catalog = [{ slug: "a", name: "Alpha", category: null, tier: 1, logo: "a.svg", logos: null }];
  const library = [
    {
      slug: "b",
      framework_name: "Beta",
      framework_slug: "b",
      page_count: 1,
      ingested_at: 1,
      tier_kind: null,
      logo: null,
      logos: ["b1.svg", "b2.svg"],
    },
  ];

  it("takes the name and logos from the catalog, which knows a framework before its first ingestion", () => {
    expect(runFramework("a", catalog, library)).toEqual({ name: "Alpha", logos: ["a.svg"] });
  });

  it("falls back to the library, then to the slug", () => {
    expect(runFramework("b", catalog, library)).toEqual({ name: "Beta", logos: ["b1.svg", "b2.svg"] });
    expect(runFramework("zeta", catalog, library)).toEqual({ name: "zeta", logos: [] });
    expect(runFramework("zeta", undefined, undefined)).toEqual({ name: "zeta", logos: [] });
  });
});

describe("runToAdopt", () => {
  const running = { slug: "alpha", run_id: "r1", progress: { status: "running" } };
  const none = new Set<string>();

  it("adopts this framework's run when the page was opened without ?run=", () => {
    expect(runToAdopt({ slug: "alpha", urlRunId: null, active: running, finished: none })).toBe("r1");
  });

  it("leaves the page alone when it already has a run, no framework, no active run, or another framework's", () => {
    expect(runToAdopt({ slug: "alpha", urlRunId: "r1", active: running, finished: none })).toBeNull();
    expect(runToAdopt({ slug: null, urlRunId: null, active: running, finished: none })).toBeNull();
    expect(runToAdopt({ slug: "alpha", urlRunId: null, active: undefined, finished: none })).toBeNull();
    expect(runToAdopt({ slug: "beta", urlRunId: null, active: running, finished: none })).toBeNull();
  });

  it("never puts back a run this page watched finish, even if the cached active list still names it", () => {
    expect(
      runToAdopt({ slug: "alpha", urlRunId: null, active: running, finished: new Set(["r1"]) }),
    ).toBeNull();
  });

  it("does not adopt a run that is already over", () => {
    const done = { ...running, progress: { status: "done" } };
    expect(runToAdopt({ slug: "alpha", urlRunId: null, active: done, finished: none })).toBeNull();
  });
});

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
    // a group sits where its first page does: the two Docs pages come first in the manifest, the root page last
    expect(groups.map((g) => g.prefix)).toEqual(["Docs", ROOT_GROUP]);
    expect(groups[1]?.items[0]?.index).toBe(2);
  });

  it("keeps the original documentation order: groups by first page, pages in manifest order, nothing sorted by name", () => {
    const pages = ["Zebra", "Apple", "Mango", "Banana", "Cherry"];
    const groups = groupEntries(
      pages.map((title, idx) =>
        entry({ idx, title, section: idx % 2 === 0 ? "Zoo" : "Ark", url: `https://x.com/docs/${title}` }),
      ),
    );
    expect(groups.map((g) => g.prefix)).toEqual(["Zoo", "Ark"]); // not alphabetical
    expect(groups.flatMap((g) => g.items.map((i) => i.entry.title))).toEqual([
      "Zebra",
      "Mango",
      "Cherry",
      "Apple",
      "Banana",
    ]);
    expect(groups[0]?.items.map((i) => i.index)).toEqual([0, 2, 4]); // ascending: source order within a group
  });

  it("skips version pins and scope roots but keeps a genuine python pair", () => {
    const groups = groupEntries([
      entry({ idx: 0, url: "https://x.com/v5.9.0/guides/setup" }),
      entry({ idx: 1, url: "https://x.com/python/commands" }),
      entry({ idx: 2, url: "https://x.com/usage/commands/config" }),
      entry({ idx: 3, url: "https://x.com" }),
    ]);
    expect(groups.map((g) => g.prefix)).toEqual(["guides", "python", "usage/commands", ROOT_GROUP]);
  });

  it("filters entries by search text and tier, and lists the tiers present", () => {
    expect(filterEntries(entries, new Set(), "Cli")).toEqual([1]);
    expect(filterEntries(entries, new Set(["llms-full"]), "")).toEqual([0]);
    expect(explorerTierValues(entries)).toEqual(["llms-full"]);
  });
});
