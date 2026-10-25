/** Drawer Overview for each planner node, built from the stats the node left in the checkpoint. */

import { fmtBytes, fmtMs } from "../format";
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

const cacheLabel = (stats: Stats, cold: string): string => (flag(stats, "cache_hit") ? "cache hit" : cold);

/** `hash 0123456789ab` when the node recorded the input hash. */
function hashNote(stats: Stats): string | null {
  const hash = text(stats, "manifest_hash").slice(0, 12);
  return hash ? `hash ${hash}` : null;
}

const compact = (parts: Array<string | null>): string[] =>
  parts.filter((part): part is string => part !== null);

function corpusLoad(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "corpus_stats");
  const files = num(s, "total_files");
  if (!files) return emptyOverview("No corpus stats reported yet.");
  const loadMs = num(s, "load_ms");
  const rate = loadMs ? Math.round((files / loadMs) * 1000) : 0;
  const ingestedAt = maybeNum(s, "ingested_at");
  const when = ingestedAt
    ? `${new Date(ingestedAt * 1000).toISOString().replace("T", " ").slice(0, 16)} UTC`
    : "—";
  return {
    cards: [
      card("Files", int(files)),
      card("Total bytes", fmtBytes(num(s, "total_bytes"))),
      card("Median page", fmtBytes(num(s, "median_bytes"))),
      card("Load time", `${loadMs} ms`, rate ? `${int(rate)} files/s` : undefined),
    ],
    sections: [
      {
        kind: "figures",
        title: "Page size distribution",
        cells: (
          [
            ["min", "min_bytes"],
            ["p10", "p10_bytes"],
            ["median", "median_bytes"],
            ["p90", "p90_bytes"],
            ["max", "max_bytes"],
          ] as const
        ).map(([key, field]) => ({ key, value: fmtBytes(num(s, field)) })),
      },
    ],
    footer: [`tier ${text(s, "tier_kind", "—")}`, `ingested ${when}`],
  };
}

function offTopic(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "off_topic_stats");
  if (maybeNum(s, "kept") === undefined && maybeNum(s, "dropped") === undefined) {
    return emptyOverview("No off-topic stats reported yet.");
  }
  const kept = num(s, "kept");
  const dropped = num(s, "dropped");
  const total = num(s, "total", kept + dropped);
  const judged = num(s, "llm_judged");
  const errors = num(s, "llm_errors");
  const decisions = records(s, "judge_decisions");
  return {
    cards: [
      card("Kept", int(kept), `of ${int(total)}`),
      card("Dropped", int(dropped), `${total ? Math.round((dropped / total) * 100) : 0}% off-topic`),
      card(
        "LLM judged",
        int(judged),
        `+keep ${num(s, "llm_kept")} · -drop ${num(s, "llm_dropped")}${errors ? ` · err ${errors}` : ""}`,
      ),
      card("Elapsed", fmtMs(num(s, "elapsed_ms")), "LLM only, no embeddings"),
    ],
    sections:
      decisions.length > 0
        ? [
            {
              kind: "table",
              title: `LLM verdict (${decisions.length} decisions)`,
              columns: ["Verdict", "Latency", "Page"],
              sortable: [0, 1, 2],
              rows: decisions.map((decision) => {
                const verdict = text(decision, "verdict", "—");
                const latency = maybeNum(decision, "latency_s");
                const failed = text(decision, "error");
                return [
                  {
                    text: failed ? `${verdict} (error)` : verdict,
                    sort: verdict,
                    tone: verdict === "KEEP" ? ("good" as const) : ("bad" as const),
                  },
                  { text: latency === undefined ? "—" : `${latency.toFixed(2)} s`, sort: latency ?? -1 },
                  { text: pathTail(text(decision, "key")), sort: pathTail(text(decision, "key")) },
                ];
              }),
            },
          ]
        : [],
    footer: [
      `LLM judge ${judged} calls (concurrency ${num(s, "judge_concurrency") || "?"})`,
      `${fmtMs(num(s, "elapsed_ms"))} total`,
    ],
  };
}

function docDistill(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "doc_distill_stats");
  const skipped = flag(s, "skipped") === true;
  const files = num(s, "n_files");
  if (!files && !skipped) return emptyOverview("No doc-distill stats reported yet.");
  const distilled = num(s, "n_distilled");
  const failed = num(s, "n_failed");
  const wallMs = num(s, "wall_ms");
  return {
    cards: [
      card(
        "Distilled",
        int(distilled),
        `${files ? Math.round((distilled / files) * 100) : 0}% of ${int(files)}`,
      ),
      card("Failed", String(failed), failed ? "rate-limited or unparsable" : "clean run"),
      card(
        "Cache",
        flag(s, "cache_hit") ? "hit" : "cold",
        flag(s, "cache_hit") ? "reused stored distillates" : "fresh distillation",
      ),
      card("Wall", wall(s), files && wallMs ? `${Math.round((files / wallMs) * 1000)} docs/s` : undefined),
    ],
    sections: skipped
      ? [
          {
            kind: "note",
            title: "Small corpus",
            text: "Distillation was skipped: the corpus is small enough for the proposer to read the raw page bodies directly.",
          },
        ]
      : [],
    footer: compact([
      hashNote(s),
      "per-doc summary + 5 key terms",
      failed ? `${failed} docs skipped, downstream still works on ${distilled}` : null,
    ]),
  };
}

function chapterPropose(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "propose_stats");
  const titles = texts(s, "titles");
  if (!num(s, "n_proposals") && titles.length === 0)
    return emptyOverview("No chapter-propose stats reported yet.");
  const valid = maybeNum(s, "n_samples_valid");
  return {
    cards: [
      card("Proposals", String(num(s, "n_proposals")), "candidate chapters"),
      card(
        "Samples OK",
        valid === undefined ? "?" : `${valid}/3`,
        `voted winner: sample ${maybeNum(s, "chosen_idx") ?? "?"}`,
      ),
      card("From docs", int(num(s, "n_files")), "distillates + structural seeds"),
      card("Wall", wall(s), cacheLabel(s, "cold")),
    ],
    sections:
      titles.length > 0 ? [{ kind: "list", title: "Proposed chapters (chosen sample)", items: titles }] : [],
    footer: compact([hashNote(s), "long-context LLM call", "3 samples + vote"]),
  };
}

function chapterAssign(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "assign_stats");
  const docs = num(s, "n_docs");
  if (!docs) return emptyOverview("No chapter-assign stats reported yet.");
  const failed = num(s, "n_failed");
  const titles = texts(statsOf(values, "propose_stats"), "titles");
  const coverage = statsOf(s, "coverage_count");
  const rows = Object.entries(coverage)
    .map(([index, count]) => ({
      index: Number(index),
      count: typeof count === "number" ? count : Number(count) || 0,
    }))
    .filter((entry) => Number.isInteger(entry.index))
    .sort((a, b) => b.count - a.count);
  return {
    cards: [
      card("Assigned", int(num(s, "n_assigned")), `of ${int(docs)} docs`),
      card("Proposals", String(num(s, "n_proposals")), "each doc scored against all"),
      card("Failed", String(failed), failed ? "rate-limited or unparsable" : "clean run"),
      card("Wall", wall(s), cacheLabel(s, "cold")),
    ],
    sections:
      rows.length > 0
        ? [
            {
              kind: "bars",
              title: "Coverage per proposal (docs with confidence ≥ 0.5)",
              rows: rows.map(({ index, count }) => ({
                id: String(index),
                label: titles[index] ?? `proposal #${index}`,
                value: count,
                valueLabel: `${count} docs`,
              })),
            },
          ]
        : [],
    footer: compact([hashNote(s), "per-doc LLM call · concurrency 8"]),
  };
}

function chapterSelect(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "select_stats");
  const titles = texts(s, "chapter_titles");
  const sizes = s.chapter_sizes;
  if (!num(s, "n_chapters_out") && titles.length === 0)
    return emptyOverview("No chapter-select stats reported yet.");
  const coverage = maybeNum(s, "coverage_fraction");
  const pruned = num(s, "n_pruned");
  return {
    cards: [
      card("Selected", String(num(s, "n_chapters_out")), `from ${num(s, "n_proposals_in")} proposals`),
      card("Pruned", String(pruned), pruned ? "<3 docs, unpinned" : "all kept"),
      card(
        "Coverage",
        coverage === undefined ? "?" : percent(coverage),
        `${num(s, "n_assigned_docs")} of ${num(s, "n_total_docs")} docs`,
      ),
      card("Wall", wall(s), "pure algorithm"),
    ],
    sections:
      titles.length > 0
        ? [
            {
              kind: "bars",
              title: `Final chapter set (${titles.length}, balanced)`,
              rows: titles.map((title, index) => {
                const size = Array.isArray(sizes) && typeof sizes[index] === "number" ? sizes[index] : 0;
                return {
                  id: String(index),
                  label: `ch-${String(index + 1).padStart(2, "0")} · ${title}`,
                  value: size,
                  valueLabel: `${size} docs`,
                };
              }),
            },
          ]
        : [],
    footer: compact([hashNote(s), "greedy coverage (≥95% target, <3-doc prune)", "no LLM"]),
  };
}

function orderChapters(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "order_chapters_stats");
  const order = (Array.isArray(s.order) ? s.order : []).filter(
    (item): item is number => typeof item === "number",
  );
  if (!num(s, "n_chapters") && !flag(s, "cache_hit") && order.length === 0) {
    return emptyOverview("No order-chapters stats reported yet.");
  }
  const foundational = Array.isArray(s.foundational) ? s.foundational.length : 0;
  const proposed = texts(statsOf(values, "propose_stats"), "titles");
  const titles = proposed.length > 0 ? proposed : texts(s, "proposal_titles");
  return {
    cards: [
      card("Chapters", String(num(s, "n_chapters", order.length)), "ranked by the panel"),
      card("Samples", String(num(s, "n_samples")), "independent rank votes"),
      card(
        "Foundational",
        String(foundational),
        foundational ? "pinned to the front" : "no pin (panel chose flat)",
      ),
      card("Wall", wall(s), flag(s, "cache_hit") ? "cache hit" : "Borda aggregate"),
    ],
    sections:
      order.length > 0
        ? [
            {
              kind: "table",
              title: `Final ordering (${order.length} chapters)`,
              columns: ["Position", "Chapter", "Was"],
              rows: order.map((original, position) => [
                { text: `pos ${position + 1}` },
                { text: titles[original] ?? `proposal #${original}` },
                { text: `#${original}${original === position ? "" : " (moved)"}` },
              ]),
            },
          ]
        : [],
    footer: [`prompt ${text(s, "prompt_version", "?")}`, `panel of ${num(s, "n_samples")} + Borda`],
  };
}

function planWrite(values: Record<string, unknown>): NodeOverview {
  const s = statsOf(values, "plan_write_stats");
  const plan = statsOf(s, "plan");
  const chapters = records(plan, "chapters");
  const storedAt = text(s, "store_path", typeof values.plan_path === "string" ? values.plan_path : "");
  if (chapters.length === 0) {
    return emptyOverview(
      storedAt ? `Plan stored at ${storedAt} — loading its chapters…` : "Waiting for the plan to be written…",
    );
  }
  const planStats = statsOf(plan, "stats");
  const sources = num(s, "n_sources", num(planStats, "n_sources"));
  const unassigned = num(s, "n_unassigned", num(planStats, "n_unassigned"));
  const dropped = num(s, "n_dropped", num(planStats, "n_dropped"));
  const corpusDocs = num(statsOf(plan, "provenance"), "corpus_doc_count");
  const sorted = chapters.slice().sort((a, b) => num(a, "order") - num(b, "order"));
  const prompts = Object.entries(statsOf(statsOf(plan, "provenance"), "prompt_versions"))
    .map(([key, value]) => `${key} ${String(value)}`)
    .join(", ");
  return {
    cards: [
      card("Chapters", String(chapters.length), "final ordered outline"),
      card(
        "Sources",
        String(sources),
        corpusDocs ? `of ${corpusDocs} corpus docs` : "collected from the corpus",
      ),
      card("Unassigned", String(unassigned), dropped ? `${dropped} empty chapters dropped` : "none dropped"),
      card("Wall", wall(s), cacheLabel(s, "cold")),
    ],
    sections: [
      {
        kind: "table",
        title: `Final plan (${sorted.length} chapters)`,
        columns: ["#", "Chapter", "Description", "Sources (sample)"],
        rows: sorted.map((chapter) => {
          const sourceKeys = texts(chapter, "sources");
          const sample = sourceKeys.slice(0, 4).map((key) => pathTail(key, 2));
          const more = sourceKeys.length > 4 ? [`… ${sourceKeys.length - 4} more`] : [];
          return [
            { text: String(num(chapter, "order", 0) || "?") },
            {
              text: `${text(chapter, "title", "?")}\n${text(chapter, "id")} · ${num(chapter, "n_sources", sourceKeys.length)} sources`,
            },
            { text: text(chapter, "description") },
            { text: [...sample, ...more].join("\n") },
          ];
        }),
      },
    ],
    footer: compact([
      storedAt ? `wrote ${storedAt}` : null,
      hashNote(s) ??
        (text(plan, "manifest_hash") ? `hash ${text(plan, "manifest_hash").slice(0, 12)}` : null),
      prompts ? `upstream prompts ${prompts}` : null,
    ]),
  };
}

export const PLANNER_OVERVIEWS: Record<string, (values: Record<string, unknown>) => NodeOverview> = {
  corpus_load: corpusLoad,
  off_topic: offTopic,
  doc_distill: docDistill,
  chapter_propose: chapterPropose,
  chapter_assign: chapterAssign,
  chapter_select: chapterSelect,
  order_chapters: orderChapters,
  plan_write: planWrite,
};
