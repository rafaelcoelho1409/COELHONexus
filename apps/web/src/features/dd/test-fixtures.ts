/** Checkpoint and event shapes copied from what the backend's planner and synth graphs really write
 * (`PlannerState` / `SynthState` keys and each node's `*_stats`), so tests exercise the real contract. */

export const PLANNER_IMPLEMENTED = {
  implemented: [
    "corpus_load",
    "off_topic",
    "doc_distill",
    "chapter_propose",
    "chapter_assign",
    "chapter_select",
    "order_chapters",
    "plan_write",
  ],
};

/** A finished planner run: every node committed its output key and its stats. */
export const PLANNER_FINISHED_VALUES: Record<string, unknown> = {
  raw_files: ["ingest/alpha/docs/intro.md", "ingest/alpha/docs/cli.md"],
  corpus_stats: {
    total_files: 12,
    total_bytes: 20_480,
    min_bytes: 100,
    p10_bytes: 300,
    median_bytes: 1_500,
    p90_bytes: 4_000,
    max_bytes: 9_000,
    load_ms: 40,
    tier_kind: "llms-full",
    ingested_at: 1_700_000_000,
  },
  relevant_files: ["ingest/alpha/docs/intro.md"],
  off_topic_stats: {
    kept: 10,
    dropped: 2,
    total: 12,
    elapsed_ms: 3_200,
    llm_judged: 12,
    llm_kept: 10,
    llm_dropped: 2,
    llm_errors: 0,
    judge_concurrency: 8,
    judge_decisions: [
      { key: "ingest/alpha/docs/intro.md", verdict: "KEEP", latency_s: 0.8 },
      { key: "ingest/alpha/blog/news.md", verdict: "DROP", latency_s: 1.2 },
    ],
  },
  doc_distill_ref: "planner/alpha/distill.json",
  doc_distill_stats: { n_files: 10, n_distilled: 10, n_failed: 0, wall_ms: 900, cache_hit: false },
  chapter_proposals_ref: "planner/alpha/proposals.json",
  propose_stats: {
    n_proposals: 3,
    n_samples_valid: 3,
    chosen_idx: 1,
    n_files: 10,
    wall_ms: 5_000,
    titles: ["Getting started", "Core concepts", "Deployment"],
  },
  chapter_doc_assignments_ref: "planner/alpha/assignments.json",
  assign_stats: {
    n_docs: 10,
    n_assigned: 9,
    n_failed: 0,
    n_proposals: 3,
    wall_ms: 2_000,
    coverage_count: { "0": 5, "1": 4, "2": 2 },
  },
  chapter_plan_ref: "planner/alpha/outline.json",
  select_stats: {
    n_chapters_out: 2,
    n_proposals_in: 3,
    n_pruned: 1,
    coverage_fraction: 0.9,
    n_assigned_docs: 9,
    n_total_docs: 10,
    chapter_titles: ["Getting started", "Core concepts"],
    chapter_sizes: [5, 4],
    wall_ms: 12,
  },
  chapter_order_ref: "planner/alpha/order.json",
  order_chapters_stats: {
    n_chapters: 2,
    n_samples: 3,
    order: [1, 0],
    foundational: [1],
    wall_ms: 800,
    prompt_version: "v3",
  },
  plan_path: "planner/alpha/plan-latest.json",
  plan_write_stats: {
    n_chapters: 2,
    n_sources: 9,
    n_unassigned: 1,
    wall_ms: 30,
    plan: {
      chapters: [
        {
          id: "ch-01-core",
          title: "Core concepts",
          order: 1,
          description: "The model behind it all.",
          n_sources: 4,
          sources: ["ingest/alpha/docs/a.md", "ingest/alpha/docs/b.md"],
        },
        {
          id: "ch-02-start",
          title: "Getting started",
          order: 2,
          description: "Install and run.",
          n_sources: 5,
          sources: ["ingest/alpha/docs/c.md"],
        },
      ],
    },
  },
  status: "done",
};

export const PLANNER_FINISHED_STATE = { values: PLANNER_FINISHED_VALUES, next: [] };

export const PLANNER_THREAD_ID = "docs-distiller/alpha/0f7a2c1e-0000-4000-8000-000000000001";
