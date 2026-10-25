import { describe, expect, it } from "vitest";

import { PLANNER_FINISHED_VALUES } from "../../test-fixtures";
import { PLANNER_NODES, SYNTH_NODES } from "../stages";
import { buildOverview } from "./index";
import type { NodeOverview } from "./types";

const cardValue = (overview: NodeOverview, label: string) =>
  overview.cards.find((c) => c.label === label)?.value;

describe("planner overviews on a finished checkpoint", () => {
  it("corpus load: size cards, the page-size distribution, tier and ingestion time", () => {
    const o = buildOverview("planner", "corpus_load", PLANNER_FINISHED_VALUES);
    expect(o.cards.map((c) => [c.label, c.value])).toEqual([
      ["Files", "12"],
      ["Total bytes", "20.0 KB"],
      ["Median page", "1.5 KB"],
      ["Load time", "40 ms"],
    ]);
    expect(o.cards[3]?.sub).toBe("300 files/s");
    expect(o.sections[0]).toMatchObject({ kind: "figures", title: "Page size distribution" });
    expect(o.footer).toEqual(["tier llms-full", "ingested 2023-11-14 22:13 UTC"]);
  });

  it("off topic: keep/drop cards and a sortable verdict table without any model or router column", () => {
    const o = buildOverview("planner", "off_topic", PLANNER_FINISHED_VALUES);
    expect(cardValue(o, "Kept")).toBe("10");
    expect(cardValue(o, "Dropped")).toBe("2");
    const table = o.sections[0];
    expect(table).toMatchObject({
      kind: "table",
      columns: ["Verdict", "Latency", "Page"],
      sortable: [0, 1, 2],
    });
    if (table?.kind !== "table") throw new Error("expected a table");
    expect(table.rows[0]?.map((cell) => cell.text)).toEqual(["KEEP", "0.80 s", "intro.md"]);
    expect(table.rows[1]?.[0]).toMatchObject({ text: "DROP", tone: "bad" });
    expect(JSON.stringify(o).toLowerCase()).not.toMatch(/deployment|router|rotator|bandit/);
  });

  it("doc distill, propose, assign, select and order", () => {
    expect(cardValue(buildOverview("planner", "doc_distill", PLANNER_FINISHED_VALUES), "Distilled")).toBe(
      "10",
    );
    const propose = buildOverview("planner", "chapter_propose", PLANNER_FINISHED_VALUES);
    expect(propose.sections[0]).toMatchObject({
      kind: "list",
      items: ["Getting started", "Core concepts", "Deployment"],
    });
    expect(cardValue(propose, "Samples OK")).toBe("3/3");

    const assign = buildOverview("planner", "chapter_assign", PLANNER_FINISHED_VALUES);
    const bars = assign.sections[0];
    if (bars?.kind !== "bars") throw new Error("expected bars");
    expect(bars.rows.map((r) => [r.label, r.valueLabel])).toEqual([
      ["Getting started", "5 docs"],
      ["Core concepts", "4 docs"],
      ["Deployment", "2 docs"],
    ]);

    const select = buildOverview("planner", "chapter_select", PLANNER_FINISHED_VALUES);
    expect(cardValue(select, "Coverage")).toBe("90%");
    expect(select.sections[0]).toMatchObject({ kind: "bars", title: "Final chapter set (2, balanced)" });

    const order = buildOverview("planner", "order_chapters", PLANNER_FINISHED_VALUES);
    const table = order.sections[0];
    if (table?.kind !== "table") throw new Error("expected a table");
    expect(table.rows.map((row) => row.map((cell) => cell.text))).toEqual([
      ["pos 1", "Core concepts", "#1 (moved)"],
      ["pos 2", "Getting started", "#0 (moved)"],
    ]);
  });

  it("plan write: the final plan table with a source sample per chapter", () => {
    const o = buildOverview("planner", "plan_write", PLANNER_FINISHED_VALUES);
    expect(cardValue(o, "Chapters")).toBe("2");
    const table = o.sections[0];
    if (table?.kind !== "table") throw new Error("expected a table");
    expect(table.rows[0]?.map((cell) => cell.text)).toEqual([
      "1",
      "Core concepts\nch-01-core · 4 sources",
      "The model behind it all.",
      "docs/a.md\ndocs/b.md",
    ]);
    expect(o.footer[0]).toBe("wrote planner/alpha/plan-latest.json");
  });
});

describe("overviews before a node has reported", () => {
  it("say so instead of showing zeros, for every node of both stages", () => {
    for (const node of PLANNER_NODES) {
      const o = buildOverview("planner", node.id, {});
      expect(o.empty, node.id).toBeTruthy();
      expect(o.cards, node.id).toEqual([]);
    }
    for (const node of SYNTH_NODES) {
      if (node.id === "sawc_derive") continue; // an absent derive stats dict is shown as 'no stats', checked below
      expect(buildOverview("synth", node.id, {}).empty, node.id).toBeTruthy();
    }
    expect(buildOverview("synth", "sawc_derive", {}).empty).toBeTruthy();
  });

  it("plan write waits for the plan, then points at where it is stored", () => {
    expect(buildOverview("planner", "plan_write", {}).empty).toMatch(/waiting/i);
    expect(buildOverview("planner", "plan_write", { plan_path: "p/plan.json" }).empty).toContain(
      "p/plan.json",
    );
  });

  it("an unknown node has no detail view", () => {
    expect(buildOverview("planner", "mystery", {}).empty).toMatch(/no detail view/);
  });
});

describe("synth overviews", () => {
  const values = {
    refine_iter: 2,
    outline_stats: {
      n_sections: 6,
      max_stage: 3,
      n_stages: 4,
      n_repairs: 1,
      n_removed_edges: 2,
      n_violations: 1,
      violations: [{ kind: "cycle", detail: "a→b→a" }],
      n_samples: 3,
      wall_ms: 4000,
    },
    digest_stats: {
      n_sections: 6,
      n_sections_covered: 5,
      n_sources: 20,
      n_orphan_code_refs: 1,
      over_spread_sources: [{ source: "ingest/a/docs/x.md", n_sections: 4 }],
    },
    sawc_stats: {
      n_sections: 6,
      n_completed: 5,
      n_fallback: 1,
      n_repairs: 2,
      total_subtopics: 18,
      avg_subtopics_per_section: 3,
      total_citations: 40,
      avg_explanation_words: 120.4,
    },
    derive_stats: {
      n_subtopics_total: 10,
      n_promoted: 3,
      n_rotator_fail: 1,
      attempts: [
        { subtopic_id: "s1", verdict: "promoted" },
        { subtopic_id: "s2", verdict: "rejected_ast" },
      ],
    },
    checklist_stats: {
      n_total: 10,
      n_passed: 8,
      pass_rate: 0.8,
      chapter_passed: false,
      names_failed: ["has examples", "cites sources"],
      n_pregate_passed: 4,
      n_pregate_total: 5,
      n_llm_passed: 4,
      n_llm_total: 5,
    },
    mgsr_stats: { halt: false, n_actions: 2, n_failed_criteria: 2, pass_rate: 0.8 },
    chapter_stats: {
      n_artifacts: 3,
      audit_passed: true,
      n_code_refs: 8,
      n_resolved: 8,
      rendered_chars: 12_340,
      n_sections: 6,
      n_subtopics_total: 18,
      n_citations_total: 40,
    },
  };

  it("builds each node's cards from its stats", () => {
    expect(cardValue(buildOverview("synth", "outline_sdp", values), "Sections")).toBe("6");
    expect(cardValue(buildOverview("synth", "digest_construct", values), "Coverage")).toBe("5/6");
    expect(cardValue(buildOverview("synth", "sawc_write", values), "Iteration")).toBe("iter 2/5");
    expect(cardValue(buildOverview("synth", "sawc_derive", values), "Promoted")).toBe("3");
    expect(cardValue(buildOverview("synth", "checklist_eval", values), "Pass rate")).toBe("80%");
    expect(cardValue(buildOverview("synth", "checklist_eval", values), "Chapter verdict")).toBe("fail");
    expect(cardValue(buildOverview("synth", "mgsr_replan", values), "Decision")).toBe("loop");
    expect(cardValue(buildOverview("synth", "render_audit_write", values), "Audit")).toBe("pass");
    expect(cardValue(buildOverview("synth", "render_audit_write", values), "Output size")).toBe(
      "12.3k chars",
    );
  });

  it("lists violations, over-spread sources, attempts and failed criteria", () => {
    expect(buildOverview("synth", "outline_sdp", values).sections[0]).toMatchObject({
      kind: "table",
      title: "Violations (1 total, showing 1)",
    });
    expect(buildOverview("synth", "digest_construct", values).sections[0]).toMatchObject({
      kind: "table",
      title: "Over-spread sources (1)",
    });
    expect(buildOverview("synth", "checklist_eval", values).sections[0]).toMatchObject({
      kind: "list",
      items: ["has examples", "cites sources"],
    });
    const attempts = buildOverview("synth", "sawc_derive", values).sections[0];
    if (attempts?.kind !== "table") throw new Error("expected a table");
    expect(attempts.rows.map((row) => row[1]?.tone)).toEqual(["good", "bad"]);
  });

  it("explains a disabled derive pass and a halted replan", () => {
    const disabled = buildOverview("synth", "sawc_derive", {
      derive_stats: { enabled: false, chapter_id: "ch-1", n_subtopics_total: 4 },
    });
    expect(cardValue(disabled, "Status")).toBe("disabled");
    const halted = buildOverview("synth", "mgsr_replan", {
      refine_iter: 1,
      mgsr_stats: { halt: true, halt_reason: "pass", confidence: 0.9, chapter_passed: true },
    });
    expect(cardValue(halted, "Decision")).toBe("halt");
    expect(halted.sections[0]).toMatchObject({ kind: "note" });
  });
});
