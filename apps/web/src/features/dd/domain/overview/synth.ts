/** Drawer Overview for each synth node (one chapter's graph), built from the stats the node left behind. */

import { fmtMs } from "../format";
import {
  flag,
  int,
  maybeNum,
  num,
  pathTail,
  percent,
  records,
  type Stats,
  statsOf,
  text,
  texts,
} from "./read";
import { emptyOverview, type NodeOverview, type OverviewCard } from "./types";

const card = (label: string, value: string, sub?: string): OverviewCard => ({ label, value, sub });

const wall = (stats: Stats): string => fmtMs(num(stats, "wall_ms"));

const coldOrHit = (stats: Stats): string => (flag(stats, "cache_hit") ? "cache hit" : "cold");

function hashNote(stats: Stats): string | null {
  const hash = text(stats, "manifest_hash").slice(0, 12);
  return hash ? `hash ${hash}` : null;
}

const compact = (parts: Array<string | null>): string[] =>
  parts.filter((part): part is string => part !== null);

const passRate = (stats: Stats, passed: number, total: number): string => {
  const reported = maybeNum(stats, "pass_rate");
  if (reported !== undefined) return percent(reported);
  return total ? percent(passed / total) : "—";
};

function outline(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "outline_stats");
  if (!num(s, "n_sections") && !flag(s, "cache_hit")) return emptyOverview("No outline stats reported yet.");
  const violations = Array.isArray(s.violations) ? s.violations : [];
  return {
    cards: [
      card("Sections", String(num(s, "n_sections")), "final outline"),
      card("Depth", String(num(s, "max_stage")), `staged review levels (${num(s, "n_stages")} stages)`),
      card("Repairs", String(num(s, "n_repairs")), `${num(s, "n_removed_edges")} edges pruned`),
      card("Violations", String(num(s, "n_violations")), num(s, "n_violations") ? "schema-flagged" : "clean"),
    ],
    sections:
      violations.length > 0
        ? [
            {
              kind: "table",
              title: `Violations (${violations.length} total, showing ${Math.min(12, violations.length)})`,
              columns: ["Kind", "Detail"],
              rows: violations.slice(0, 12).map((violation) => {
                const record = statsOf({ v: violation }, "v");
                return [
                  { text: text(record, "kind", "issue") },
                  {
                    text: text(
                      record,
                      "detail",
                      typeof violation === "string" ? violation : JSON.stringify(violation),
                    ),
                  },
                ];
              }),
            },
          ]
        : [],
    footer: compact([
      `${num(s, "n_samples", 1)} samples · vote`,
      `vault hashes referenced ${num(s, "n_vault_hashes")}`,
      flag(s, "truncated") ? "corpus truncated" : null,
      coldOrHit(s),
      wall(s),
      hashNote(s),
    ]),
  };
}

function digest(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "digest_stats");
  if (!num(s, "n_sections") && !flag(s, "cache_hit")) return emptyOverview("No digest stats reported yet.");
  const covered = num(s, "n_sections_covered");
  const total = num(s, "n_sections");
  const merged = num(s, "n_merged_sections");
  const over = Array.isArray(s.over_spread_sources) ? s.over_spread_sources : [];
  const pydanticFail = num(s, "n_pydantic_fail");
  return {
    cards: [
      card("Sources", String(num(s, "n_sources")), "evidence pool"),
      card("Coverage", `${covered}/${total}`, `${total ? percent(covered / total) : "—"} of sections`),
      card("Orphan refs", String(num(s, "n_orphan_code_refs")), "code blocks no section claimed"),
      card("Empty", String(num(s, "n_empty_sections")), merged ? `${merged} sections merged` : undefined),
    ],
    sections:
      over.length > 0
        ? [
            {
              kind: "table",
              title: `Over-spread sources (${over.length})`,
              columns: ["Source", "Spread"],
              rows: over.slice(0, 10).map((entry) => {
                const record = statsOf({ v: entry }, "v");
                const source = typeof entry === "string" ? entry : text(record, "source", "?");
                const sections = maybeNum(record, "n_sections");
                return [{ text: pathTail(source, 2) }, { text: `spread across ${sections ?? "?"} sections` }];
              }),
            },
          ]
        : [],
    footer: compact([
      `avg ${num(s, "avg_sources_per_section").toFixed(1)} src/section · ${num(s, "avg_sections_per_source").toFixed(1)} sections/src`,
      `vault hashes ${num(s, "n_total_vault_hashes")}`,
      pydanticFail ? `${pydanticFail} schema failures` : null,
      coldOrHit(s),
      wall(s),
      hashNote(s),
    ]),
  };
}

function sawc(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "sawc_stats");
  if (!num(s, "n_sections") && !flag(s, "cache_hit")) return emptyOverview("No writer stats reported yet.");
  const iteration = num(values, "refine_iter");
  const completed = num(s, "n_completed");
  const total = num(s, "n_sections");
  const repairs = num(s, "n_repairs");
  const pickerFallbacks = num(s, "n_picker_fallbacks");
  const density = (
    [
      ["subtopics", maybeNum(s, "total_subtopics"), (n: number) => String(n)],
      ["avg/section", maybeNum(s, "avg_subtopics_per_section"), (n: number) => n.toFixed(1)],
      ["citations", maybeNum(s, "total_citations"), (n: number) => String(n)],
      ["avg words/expl", maybeNum(s, "avg_explanation_words"), (n: number) => n.toFixed(0)],
    ] as const
  ).flatMap(([key, value, format]) => (value === undefined ? [] : [{ key, value: format(value) }]));
  return {
    cards: [
      card(
        "Completed",
        `${completed}/${total}`,
        total ? `${percent(completed / total)} sections` : undefined,
      ),
      card(
        "Iteration",
        iteration > 0 ? `iter ${iteration}/5` : "first pass",
        iteration > 1 ? "refinement loop active" : "no replan yet",
      ),
      card(
        "Fallbacks",
        String(num(s, "n_fallback")),
        pickerFallbacks ? `${pickerFallbacks} picker fallbacks` : undefined,
      ),
      card("Repairs", String(repairs), repairs ? "auto-fix passes" : "clean"),
    ],
    sections: density.length > 0 ? [{ kind: "figures", title: "Content density", cells: density }] : [],
    footer: compact([
      `stages ${num(s, "n_stages")}`,
      `drafts fired ${num(s, "n_total_drafts_fired")}`,
      `critic picks ${num(s, "n_critic_picks")}`,
      coldOrHit(s),
      wall(s),
      hashNote(s),
    ]),
  };
}

function derive(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "derive_stats");
  if (flag(s, "enabled") === false) {
    return {
      cards: [
        card("Status", "disabled", "feature flag off"),
        card("Chapter", text(s, "chapter_id", "—")),
        card("Wall", wall(s), "no LLM calls"),
        card("Subtopics", String(num(s, "n_subtopics_total")), "all kept verbatim"),
      ],
      sections: [],
      footer: [
        "This is the optional pass that derives extra analogical code examples; it is off in this deployment.",
      ],
    };
  }
  const subtopics = num(s, "n_subtopics_total");
  if (!subtopics) return emptyOverview("No derive stats reported yet.");
  const promoted = num(s, "n_promoted");
  const thin = num(s, "n_candidates_thin");
  const attempts = records(s, "attempts");
  return {
    cards: [
      card("Promoted", String(promoted), `${percent(promoted / subtopics)} of ${subtopics} subtopics`),
      card("AST reject", String(num(s, "n_rejected_ast")), "parse failures"),
      card("Length reject", String(num(s, "n_rejected_len")), "too-short candidates"),
      card("LLM fail", String(num(s, "n_rotator_fail")), thin ? `${thin} thin candidates` : "clean"),
    ],
    sections:
      attempts.length > 0
        ? [
            {
              kind: "table",
              title: `Attempt log (showing ${Math.min(12, attempts.length)} of ${attempts.length})`,
              columns: ["Subtopic", "Verdict"],
              rows: attempts.slice(0, 12).map((attempt) => {
                const verdict = text(attempt, "verdict", text(attempt, "status", "—"));
                return [
                  { text: text(attempt, "subtopic_id", "?") },
                  {
                    text: verdict,
                    tone:
                      verdict === "promoted"
                        ? ("good" as const)
                        : verdict.startsWith("rejected")
                          ? ("bad" as const)
                          : undefined,
                  },
                ];
              }),
            },
          ]
        : [],
    footer: [`chapter ${text(s, "chapter_id", "?")}`, `schema ${text(s, "schema_version", "?")}`, wall(s)],
  };
}

function checklist(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "checklist_stats");
  if (!num(s, "n_total") && !flag(s, "cache_hit")) return emptyOverview("No checklist stats reported yet.");
  const passed = num(s, "n_passed");
  const total = num(s, "n_total");
  const verdict = flag(s, "chapter_passed");
  const failed = texts(s, "names_failed");
  const repaired = num(s, "judge_repaired");
  const judgeMs = maybeNum(s, "judge_wall_ms");
  return {
    cards: [
      card("Pass rate", passRate(s, passed, total), `${passed} / ${total} criteria`),
      card(
        "Chapter verdict",
        verdict === true ? "pass" : verdict === false ? "fail" : "—",
        verdict === true ? "all criteria met" : verdict === false ? "below threshold" : "pending",
      ),
      card("Pre-gate", `${num(s, "n_pregate_passed")}/${num(s, "n_pregate_total")}`, "deterministic checks"),
      card(
        "LLM judged",
        `${num(s, "n_llm_passed")}/${num(s, "n_llm_total")}`,
        repaired ? `${repaired} repaired` : undefined,
      ),
    ],
    sections:
      failed.length > 0
        ? [{ kind: "list", title: `Failed criteria (${failed.length})`, items: failed.slice(0, 20) }]
        : [],
    footer: compact([
      `feedback generated ${num(s, "n_failed_feedback")}`,
      judgeMs === undefined ? null : `judge ${fmtMs(judgeMs)}`,
      coldOrHit(s),
      wall(s),
      hashNote(s),
    ]),
  };
}

function mgsr(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "mgsr_stats");
  const halt = flag(s, "halt");
  if (halt === undefined && !flag(s, "cache_hit")) return emptyOverview("No replan stats reported yet.");
  const rate = maybeNum(s, "pass_rate");
  if (flag(s, "skipped") === true) {
    return {
      cards: [
        card("Analysis", "skipped"),
        card("Pass rate", rate === undefined ? "—" : percent(rate), "from the checklist"),
      ],
      sections: [
        {
          kind: "note",
          title: "Decision summary",
          text: "The corrective-actions analysis is currently skipped (nothing consumes its output). The retry-or-stop decision is made by the synth graph itself from the checklist's pass rate; the checklist node's log line shows what happens next.",
        },
      ],
      footer: compact([wall(s), hashNote(s)]),
    };
  }
  const confidence = maybeNum(s, "confidence");
  const iteration = num(values, "refine_iter", 1);
  const actions = num(s, "n_actions");
  return {
    cards: [
      card("Decision", halt ? "halt" : "loop", halt ? "chapter accepted" : "re-enter the writer"),
      card(
        "Reason",
        text(s, "halt_reason", "—"),
        flag(s, "trivial_pass") ? "trivial-pass shortcut" : undefined,
      ),
      card("Confidence", confidence === undefined ? "—" : percent(confidence), "judge confidence"),
      card("Actions", String(actions), `${num(s, "n_failed_criteria")} failed criteria addressed`),
    ],
    sections: [
      {
        kind: "note",
        title: "Decision summary",
        text: halt
          ? `Chapter halted at iteration ${iteration}/5 with confidence ${confidence === undefined ? "—" : percent(confidence)}${flag(s, "chapter_passed") === true ? "; it passed the checklist gate" : ""}. Reason: ${text(s, "halt_reason", "?")}.`
          : `Chapter loops back to the writer (iteration ${iteration}/5). Pass rate ${rate === undefined ? "—" : percent(rate)}; ${actions} refine action(s) queued.`,
      },
    ],
    footer: compact([coldOrHit(s), wall(s), hashNote(s)]),
  };
}

function renderAudit(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "chapter_stats");
  if (!num(s, "n_artifacts") && !flag(s, "cache_hit"))
    return emptyOverview("No render/audit stats reported yet.");
  const audit = flag(s, "audit_passed");
  const refs = num(s, "n_code_refs");
  const resolved = num(s, "n_resolved");
  const missing = num(s, "n_missing");
  const drift = num(s, "n_byte_drift");
  return {
    cards: [
      card(
        "Audit",
        audit === true ? "pass" : audit === false ? "fail" : "—",
        drift ? `${drift} byte drift` : missing ? `${missing} missing refs` : "clean",
      ),
      card("Code refs", `${resolved}/${refs}`, refs ? `${percent(resolved / refs)} resolved` : "none cited"),
      card(
        "Output size",
        `${(num(s, "rendered_chars") / 1000).toFixed(1)}k chars`,
        `${num(s, "n_sections")} sections, ${num(s, "n_subtopics_total")} subtopics`,
      ),
      card("Artifacts", String(num(s, "n_artifacts")), `${num(s, "n_citations_total")} citations`),
    ],
    sections: [
      {
        kind: "figures",
        title: "Vault usage",
        cells: [
          { key: "files loaded", value: int(num(s, "n_vault_files_loaded")) },
          { key: "files skipped", value: int(num(s, "n_vault_files_skipped")) },
          { key: "entries", value: int(num(s, "n_vault_entries")) },
          { key: "orphan", value: int(num(s, "n_orphan_unused")) },
          { key: "sentinels", value: int(num(s, "sentinels_in_output")) },
        ],
      },
    ],
    footer: compact([
      `template ${text(s, "template_version", "?")}`,
      text(s, "readme_path") ? `path ${text(s, "readme_path")}` : null,
      coldOrHit(s),
      wall(s),
      hashNote(s),
    ]),
  };
}

export const SYNTH_OVERVIEWS: Record<string, (values: Record<string, unknown>) => NodeOverview> = {
  outline_sdp: outline,
  digest_construct: digest,
  sawc_write: sawc,
  sawc_derive: derive,
  checklist_eval: checklist,
  mgsr_replan: mgsr,
  render_audit_write: renderAudit,
};
