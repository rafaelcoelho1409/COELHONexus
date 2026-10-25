/** What each planner/synth node does, in plain words, plus the three headline numbers it left in the
 * checkpoint and the raw state it reads and writes (the drawer's Overview header and Raw I/O tab). */

import type { RunStage } from "../params";
import { fmtBytes } from "./format";
import { flag, maybeNum, num, percent, type Stats, statsOf } from "./overview/read";

export type NodeMetric = { label: string; value: string; note?: string };

export type NodeDetails = {
  title: string;
  subtitle: string;
  kind: string;
  actions: string[];
  /** State keys this node reads / writes (what the Raw I/O tab shows). */
  inputs: string[];
  outputs: string[];
  /** Whether and how this node calls an LLM. */
  llm: string;
  metrics: (values: Record<string, unknown>) => NodeMetric[];
};

const metric = (label: string, value: string | number, note?: string): NodeMetric => ({
  label,
  value: String(value),
  note,
});

/** The count alone: the metric's own label already says "calls" ("78 LLM calls", not "78 calls LLM calls"). */
const countOrFallback = (n: number | undefined, fallback: string): string =>
  n === undefined ? fallback : String(n);

const PLANNER_DETAILS: Record<string, NodeDetails> = {
  corpus_load: {
    title: "Corpus load",
    subtitle: "Reads the selected documentation corpus from storage.",
    kind: "deterministic I/O",
    actions: [
      "Loads the raw files for the active framework.",
      "Computes corpus size, file count, byte distribution and ingestion metadata.",
      "Publishes raw_files and corpus_stats for the nodes after it.",
    ],
    inputs: ["framework_slug"],
    outputs: ["raw_files", "corpus_stats"],
    llm: "No LLM call. This node is storage and metadata work only.",
    metrics: (values) => {
      const s = statsOf(values, "corpus_stats");
      return [
        metric("files", num(s, "total_files", num(s, "files"))),
        metric("bytes", fmtBytes(num(s, "total_bytes"))),
        metric("LLM calls", 0, "no model involved"),
      ];
    },
  },
  off_topic: {
    title: "Off-topic filter",
    subtitle: "An LLM judge keeps only the pages relevant to the framework.",
    kind: "LLM judge",
    actions: [
      "Judges each document against the framework's domain boundary.",
      "Records KEEP/DROP decisions with latency and error telemetry.",
      "Writes the filtered relevant_files set the planning nodes use.",
    ],
    inputs: ["raw_files"],
    outputs: ["relevant_files", "off_topic_stats"],
    llm: "One LLM judge call per document.",
    metrics: (values) => {
      const s = statsOf(values, "off_topic_stats");
      return [
        metric("LLM calls", countOrFallback(maybeNum(s, "llm_judged"), "pending")),
        metric(
          "kept/dropped",
          `${num(s, "llm_kept", num(s, "kept"))}/${num(s, "llm_dropped", num(s, "dropped"))}`,
        ),
        metric("LLM errors", num(s, "llm_errors")),
      ];
    },
  },
  doc_distill: {
    title: "Document distill",
    subtitle: "Compresses large corpora into per-document summaries and key terms.",
    kind: "LLM map",
    actions: [
      "For large corpora, summarises each relevant document and extracts key terms.",
      "For small corpora, skips distillation so the proposer reads raw bodies directly.",
      "Persists the distillates plus stats about success, failures, cache and wall time.",
    ],
    inputs: ["relevant_files"],
    outputs: ["doc_distill_ref", "doc_distill_stats"],
    llm: "Parallel per-document LLM calls, unless the small-corpus bypass is active.",
    metrics: (values) => {
      const s = statsOf(values, "doc_distill_stats");
      if (flag(s, "skipped")) {
        return [
          metric("LLM calls", 0, "small-corpus bypass"),
          metric("files", num(s, "n_files")),
          metric("cache", flag(s, "cache_hit") ? "hit" : "not used"),
        ];
      }
      return [
        metric("distilled", num(s, "n_distilled")),
        metric("failed", num(s, "n_failed")),
        metric("cache", flag(s, "cache_hit") ? "hit" : "cold"),
      ];
    },
  },
  chapter_propose: {
    title: "Chapter propose",
    subtitle: "Asks the LLM to propose candidate study chapters.",
    kind: "LLM synthesis",
    actions: [
      "Builds a long-context planning prompt from distillates, headings and the file-tree structure.",
      "Samples candidate chapter sets and picks the best with a consistency vote.",
      "Writes the proposals with their provenance.",
    ],
    inputs: ["doc_distill_ref", "relevant_files"],
    outputs: ["chapter_proposals_ref", "propose_stats"],
    llm: "Long-context LLM sampling, plus vote and repair calls when needed.",
    metrics: (values) => {
      const s = statsOf(values, "propose_stats");
      const valid = maybeNum(s, "n_samples_valid");
      return [
        metric("proposals", num(s, "n_proposals")),
        metric("valid samples", valid === undefined ? "pending" : `${valid}/3`),
        metric("chosen sample", maybeNum(s, "chosen_idx") ?? "pending"),
      ];
    },
  },
  chapter_assign: {
    title: "Chapter assign",
    subtitle: "Scores every document against every proposed chapter.",
    kind: "LLM classifier",
    actions: [
      "Runs a structured LLM call per document that returns chapter confidence scores.",
      "Allows a page to belong to more than one chapter.",
      "Falls back to a lexical match only when a document has content but its call fails.",
    ],
    inputs: ["chapter_proposals_ref", "doc_distill_ref", "relevant_files"],
    outputs: ["chapter_doc_assignments_ref", "assign_stats"],
    llm: "One short structured LLM call per document, with bounded concurrency.",
    metrics: (values) => {
      const s = statsOf(values, "assign_stats");
      return [
        metric("docs scored", num(s, "n_docs")),
        metric("assigned", num(s, "n_assigned")),
        metric("failed", num(s, "n_failed")),
      ];
    },
  },
  chapter_select: {
    title: "Chapter select",
    subtitle: "Chooses the final chapter set with deterministic coverage logic.",
    kind: "algorithm",
    actions: [
      "Picks a minimum useful chapter set from the proposal/assignment matrix.",
      "Targets broad document coverage while pruning tiny unpinned chapters.",
      "Produces the chapter plan the ordering and plan-writing nodes consume.",
    ],
    inputs: ["chapter_doc_assignments_ref"],
    outputs: ["chapter_plan_ref", "select_stats"],
    llm: "No LLM call. Selection is greedy coverage and pruning.",
    metrics: (values) => {
      const s = statsOf(values, "select_stats");
      const coverage = maybeNum(s, "coverage_fraction");
      return [
        metric("chapters out", num(s, "n_chapters_out")),
        metric("coverage", coverage === undefined ? "pending" : percent(coverage)),
        metric("pruned", num(s, "n_pruned")),
      ];
    },
  },
  order_chapters: {
    title: "Order chapters",
    subtitle: "Ranks the selected chapters into a teaching sequence.",
    kind: "LLM ranking",
    actions: [
      "Samples independent chapter orderings from the LLM.",
      "Pins foundational chapters first when the panel identifies prerequisites.",
      "Aggregates the samples with Borda scoring into the final order.",
    ],
    inputs: ["chapter_plan_ref"],
    outputs: ["chapter_order_ref", "order_chapters_stats"],
    llm: "A small panel of LLM ranking calls plus deterministic Borda aggregation.",
    metrics: (values) => {
      const s = statsOf(values, "order_chapters_stats");
      return [
        metric("ranking calls", countOrFallback(maybeNum(s, "n_samples"), "pending")),
        metric("chapters", num(s, "n_chapters")),
        metric("cache", flag(s, "cache_hit") ? "hit" : "cold"),
      ];
    },
  },
  plan_write: {
    title: "Plan write",
    subtitle: "Writes the final study plan that Synth and the reader use.",
    kind: "deterministic writer",
    actions: [
      "Combines the selected, ordered chapters with their hydrated source references.",
      "Drops empty chapters and records unassigned and dropped source counts.",
      "Writes the plan and the latest-plan pointer used by Synth and Study.",
    ],
    inputs: ["chapter_order_ref", "chapter_plan_ref"],
    outputs: ["plan_path", "plan_write_stats"],
    llm: "No LLM call. This node formats and persists the final plan.",
    metrics: (values) => {
      const s = statsOf(values, "plan_write_stats");
      const planned = statsOf(s, "plan").chapters;
      return [
        metric("chapters", num(s, "n_chapters", Array.isArray(planned) ? planned.length : 0)),
        metric("sources", num(s, "n_sources")),
        metric("unassigned", num(s, "n_unassigned")),
      ];
    },
  },
};

const SYNTH_DETAILS: Record<string, NodeDetails> = {
  outline_sdp: {
    title: "Outline (structure-driven planner)",
    subtitle: "Builds a typed section graph for one chapter.",
    kind: "LLM planner",
    actions: [
      "Asks the outline planner for sections and their prerequisites.",
      "Derives a dependency graph, stage indices, cycle repairs and structural validation.",
      "Hands same-stage parallelism to the writer node.",
    ],
    inputs: ["framework_slug", "chapter_id"],
    outputs: ["outline_path", "outline_stats"],
    llm: "An LLM outline call plus deterministic graph derivation.",
    metrics: (values) => {
      const s = statsOf(values, "outline_stats");
      return [
        metric("samples", num(s, "n_samples", 1)),
        metric("sections", num(s, "n_sections")),
        metric("violations", num(s, "n_violations")),
      ];
    },
  },
  digest_construct: {
    title: "Digest construct",
    subtitle: "Routes evidence, source summaries and code references into outline sections.",
    kind: "evidence builder",
    actions: [
      "Builds the per-section evidence digest the writer consumes.",
      "Routes source documents and vault code references to the sections that need them.",
      "Flags orphan refs, empty sections and over-spread sources for review.",
    ],
    inputs: ["outline_path"],
    outputs: ["digest_path", "digest_stats"],
    llm: "LLM-assisted routing where configured; deterministic aggregation produces the coverage stats.",
    metrics: (values) => {
      const s = statsOf(values, "digest_stats");
      return [
        metric("sources", num(s, "n_sources")),
        metric("coverage", `${num(s, "n_sections_covered")}/${num(s, "n_sections")}`),
        metric("orphan refs", num(s, "n_orphan_code_refs")),
      ];
    },
  },
  sawc_write: {
    title: "SAWC write (section-aware writer-critic)",
    subtitle: "Writes chapter sections with staged drafts, critic selection and repairs.",
    kind: "LLM writer/critic",
    actions: [
      "Runs stage-parallel best-of-N section drafts against the outline and digest.",
      "Has a critic pick the best draft and records fallback decisions.",
      "Extracts memory for later sections and re-enters when the replanner asks for refinement.",
    ],
    inputs: ["outline_path", "digest_path"],
    outputs: ["sawc_path", "sawc_stats"],
    llm: "A section-level writer plus a critic-picker for the material.",
    metrics: (values) => {
      const s = statsOf(values, "sawc_stats");
      return [
        metric("draft calls", num(s, "n_total_drafts_fired")),
        metric("critic picks", num(s, "n_critic_picks")),
        metric("repairs", num(s, "n_repairs")),
      ];
    },
  },
  sawc_derive: {
    title: "SAWC derive",
    subtitle: "Optionally derives extra analogical code examples for subtopics.",
    kind: "optional LLM expansion",
    actions: [
      "Attempts analogical code generation for eligible subtopics.",
      "Rejects candidates that fail the syntax, length or quality gates.",
      "Promotes accepted examples into the chapter.",
    ],
    inputs: ["sawc_path"],
    outputs: ["derive_stats"],
    llm: "Optional LLM calls, controlled by a feature flag.",
    metrics: (values) => {
      const s = statsOf(values, "derive_stats");
      if (flag(s, "enabled") === false) {
        return [
          metric("LLM calls", 0, "disabled"),
          metric("subtopics", num(s, "n_subtopics_total")),
          metric("promoted", 0),
        ];
      }
      return [
        metric("promoted", num(s, "n_promoted")),
        metric("LLM fail", num(s, "n_rotator_fail")),
        metric("attempts", Array.isArray(s.attempts) ? s.attempts.length : "not reported"),
      ];
    },
  },
  checklist_eval: {
    title: "Checklist eval",
    subtitle: "Evaluates the drafted chapter against deterministic and LLM criteria.",
    kind: "hybrid evaluator",
    actions: [
      "Runs pre-gate deterministic checks before spending judge calls.",
      "Uses an LLM judge for the criteria that need semantic reading.",
      "Produces the pass/fail feedback the replanner uses.",
    ],
    inputs: ["sawc_path"],
    outputs: ["checklist_path", "checklist_stats"],
    llm: "LLM judge calls only for the criteria that survive the pre-gate.",
    metrics: (values) => {
      const s = statsOf(values, "checklist_stats");
      const rate = maybeNum(s, "pass_rate");
      return [
        metric("LLM judged", `${num(s, "n_llm_passed")}/${num(s, "n_llm_total")}`),
        metric("pre-gate", `${num(s, "n_pregate_passed")}/${num(s, "n_pregate_total")}`),
        metric("pass rate", rate === undefined ? "pending" : percent(rate)),
      ];
    },
  },
  mgsr_replan: {
    title: "Memory-guided structure replanner",
    subtitle: "Decides whether the chapter stops or loops back for refinement.",
    kind: "LLM replan",
    actions: [
      "Short-circuits without an LLM call when the checklist already passes.",
      "Otherwise asks for typed refinement actions aimed at the failed criteria.",
      "Routes the graph either to render and audit, or back to the writer.",
    ],
    inputs: ["checklist_path", "sawc_path"],
    outputs: ["mgsr_path", "mgsr_stats"],
    llm: "The fast path makes no LLM call; the replan path makes one structured call.",
    metrics: (values) => {
      const s = statsOf(values, "mgsr_stats");
      const halt = flag(s, "halt");
      return [
        metric("decision", halt === undefined ? "pending" : halt ? "halt" : "loop"),
        metric("LLM path", flag(s, "trivial_pass") ? "0 calls" : halt === undefined ? "pending" : "1 call"),
        metric("actions", num(s, "n_actions")),
      ];
    },
  },
  render_audit_write: {
    title: "Render and audit write",
    subtitle: "Renders the final chapter and verifies code-reference integrity.",
    kind: "deterministic render",
    actions: [
      "Renders the final markdown from the writer's output and the resolved vault references.",
      "Audits reference resolution, sentinel replacement, byte drift and missing refs.",
      "Writes the chapter that Study reads.",
    ],
    inputs: ["sawc_path", "derive_stats", "mgsr_path"],
    outputs: ["chapter_path", "chapter_stats"],
    llm: "No LLM call. This node renders, audits and persists artifacts.",
    metrics: (values) => {
      const s = statsOf(values, "chapter_stats");
      const audit = flag(s, "audit_passed");
      return [
        metric("audit", audit === undefined ? "pending" : audit ? "pass" : "fail"),
        metric("refs", `${num(s, "n_resolved")}/${num(s, "n_code_refs")}`),
        metric("artifacts", num(s, "n_artifacts")),
      ];
    },
  },
};

const DETAILS: Record<RunStage, Record<string, NodeDetails>> = {
  planner: PLANNER_DETAILS,
  synth: SYNTH_DETAILS,
};

export function nodeDetails(stage: RunStage, nodeId: string): NodeDetails | null {
  return DETAILS[stage][nodeId] ?? null;
}

/** The node's three headline numbers; empty when the node is unknown. */
export function nodeMetrics(stage: RunStage, nodeId: string, values: Record<string, unknown>): NodeMetric[] {
  return nodeDetails(stage, nodeId)?.metrics(values) ?? [];
}

/** The node's first headline number as one short line for its card ("1,200 files"). */
export function nodeKpi(stage: RunStage, nodeId: string, values: Record<string, unknown>): string | null {
  const first = nodeMetrics(stage, nodeId, values)[0];
  return first ? `${first.value} ${first.label}` : null;
}

const MAX_RAW_CHARS = 20_000;

function pretty(values: Record<string, unknown>, keys: readonly string[]): string | null {
  const picked: Stats = {};
  for (const key of keys) if (key in values) picked[key] = values[key];
  if (Object.keys(picked).length === 0) return null;
  const json = JSON.stringify(picked, null, 2);
  return json.length > MAX_RAW_CHARS ? `${json.slice(0, MAX_RAW_CHARS)}\n… truncated` : json;
}

/** The raw state this node reads and writes, as formatted JSON (null when none of it exists yet). */
export function nodeRawIO(
  stage: RunStage,
  nodeId: string,
  values: Record<string, unknown>,
): { inputs: string | null; outputs: string | null } {
  const details = nodeDetails(stage, nodeId);
  if (!details) return { inputs: null, outputs: null };
  return { inputs: pretty(values, details.inputs), outputs: pretty(values, details.outputs) };
}
