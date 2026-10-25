import { describe, expect, it } from "vitest";

import {
  activeRunsSchema,
  graphStateSchema,
  libraryListSchema,
  manifestSchema,
  runStatusSchema,
} from "./entities";

describe("dd entities", () => {
  it("parses the library list and defaults the optional fields of a sparse row", () => {
    const parsed = libraryListSchema.safeParse([
      {
        slug: "fasthtml",
        framework_name: "FastHTML",
        page_count: 120,
        ingested_at: 1700,
        tier_kind: "llms-full",
      },
      { slug: "broken" },
    ]);
    expect(parsed.success).toBe(true);
    if (parsed.success) expect(parsed.data[1]?.page_count).toBeNull();
  });

  it("parses a manifest with section-carrying entries", () => {
    const parsed = manifestSchema.safeParse({
      framework_name: "FastHTML",
      entries: [{ idx: 0, title: "Intro", tier: "llms-full", bytes: 2048, section: "Documentation" }],
      total_bytes: 2048,
      tier_kind: "llms-full",
      ingested_at: 1700,
    });
    expect(parsed.success).toBe(true);
  });

  it("parses run status with a split post summary", () => {
    const parsed = runStatusSchema.safeParse({
      progress: { tier: "docs", status: "running", current: 3, total: 10, last_url: "u" },
      post: { was_split: true, input_files: 5, output_files: 40, duplicates_dropped: 2, stubs_dropped: 1 },
    });
    expect(parsed.success).toBe(true);
  });

  it("reads the scheduled nodes of a graph state from either endpoint's spelling", () => {
    const planner = graphStateSchema.parse({ thread_id: "t", next_nodes: ["off_topic"], values: { a: 1 } });
    expect(planner.next).toEqual(["off_topic"]);
    const synth = graphStateSchema.parse({ thread_id: "t", next: ["sawc_write"], values: {} });
    expect(synth.next).toEqual(["sawc_write"]);
    expect(graphStateSchema.parse({}).next).toEqual([]);
    expect(graphStateSchema.parse({ values: 5 }).values).toEqual({});
  });

  it("parses active runs", () => {
    expect(
      activeRunsSchema.safeParse({ active: [{ run_id: "r1", slug: "fasthtml", progress: null }] }).success,
    ).toBe(true);
  });
});
