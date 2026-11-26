import type { AgenticProgress, ChapterRange } from "../entities";

export const AGENTIC_NODES = [
  { id: "audit_batch", label: "Audit pages" },
  { id: "classify_content", label: "Select Distill pages" },
  { id: "normalize_metadata", label: "Normalize metadata" },
  { id: "plan_repairs", label: "Plan repairs" },
  { id: "repair_pages", label: "Repair pages" },
  { id: "enrich_visuals", label: "Convert source figures" },
  { id: "materialize_processed_book", label: "Build processed pages" },
  { id: "reconcile_contents", label: "Reconcile contents" },
  { id: "publish", label: "Publish results" },
] as const;

export type AgenticNodeId = (typeof AGENTIC_NODES)[number]["id"];
export type AgenticNodeStatus = "pending" | "running" | "done" | "failed" | "cancelled";

/** What each node does, in plain words (the sheet's Overview), and what it reads, writes and asks of a model. */
export type AgenticNodeDetails = {
  title: string;
  subtitle: string;
  kind: string;
  actions: readonly string[];
  reads: readonly string[];
  writes: readonly string[];
  llm: string;
  resources: AgenticNodeResources;
};

export type AgenticNodeResources = {
  agents: readonly string[];
  skills: readonly string[];
  tools: readonly string[];
};

export type AgenticNodeProgress = {
  current: number;
  total: number;
  label: string;
  active: number;
  activeLabel?: string;
};

const noResources: AgenticNodeResources = { agents: [], skills: [], tools: [] };

export const AGENTIC_NODE_DETAILS: Record<AgenticNodeId, AgenticNodeDetails> = {
  audit_batch: {
    title: "Audit pages",
    subtitle:
      "A page-auditor agent reads every extracted page and reports extraction defects with exact evidence.",
    kind: "LLM audit + deterministic checks",
    actions: [
      "Runs deterministic checks first: blank and image-only pages, malformed tables, spaced headings, control characters.",
      "Asks the page auditor about each text page, many pages at a time.",
      "Keeps a finding only when its evidence is an exact excerpt of the page and supports the issue type; a page whose audit fails keeps its deterministic findings and is marked unverified.",
    ],
    reads: ["extracted pages"],
    writes: ["page assessments"],
    llm: "Page auditor agent, one call per page, retried before a page is marked unverified.",
    resources: {
      agents: ["page_auditor", "finding_verifier"],
      skills: ["audit-docling-page"],
      tools: ["read_file"],
    },
  },
  plan_repairs: {
    title: "Plan repairs",
    subtitle: "Routes findings on selected pages to the specialist that handles each issue type.",
    kind: "deterministic routing",
    actions: [
      "Skips pages excluded by the pre-repair content selection.",
      "Sends split words, fused words and corrupted text to the text specialist.",
      "Sends layout, contents and table findings to the structure specialist.",
      "Leaves control characters and soft hyphens to code, which removes them without a model.",
    ],
    reads: ["page assessments", "versioned Distill page selection"],
    writes: ["repair work items"],
    llm: "No model call. The routing is a fixed table.",
    resources: noResources,
  },
  repair_pages: {
    title: "Repair pages",
    subtitle: "Repairs selected pages; excluded pages receive a source-preserving pass-through record.",
    kind: "LLM specialists + safe patching",
    actions: [
      "Removes control characters and soft hyphens, and tidies table padding, in code.",
      "Applies deterministic safe fixes directly; checks eligible text corrections against a rendered image of the original PDF page.",
      "Uses the literal source reading to refine a low-risk spacing or punctuation patch when its letters match both the extracted span and proposed correction; applies it only after token, overlap and source-scope safeguards.",
      "Preserves the original text and reports unresolved findings when verification is uncertain; no user approval is required.",
    ],
    reads: ["selected extracted pages", "page assessments", "repair work items", "original PDF page images"],
    writes: ["AI-processed pages", "patch proposals"],
    llm: "Text-repair and structure-repair agents propose changes; a source-page verifier checks eligible text proposals against the original PDF.",
    resources: {
      agents: ["text_repair", "structure_repair", "text_patch_verifier"],
      skills: ["propose-conservative-text-repair", "repair-markdown-structure"],
      tools: ["read_file", "check_patches", "book_term_frequency", "adjacent_page_edges"],
    },
  },
  normalize_metadata: {
    title: "Normalize metadata",
    subtitle: "Reads the front matter and proposes the book's title, subtitle and authors.",
    kind: "LLM extraction",
    actions: [
      "Reads the first pages of the book.",
      "Accepts a proposal only when its evidence is quoted from those pages and its confidence is high; otherwise falls back to the file name and asks for review.",
    ],
    reads: ["first extracted pages", "file name"],
    writes: ["book metadata"],
    llm: "Metadata normalizer agent, one call.",
    resources: {
      agents: ["metadata_normalizer"],
      skills: ["normalize-book-metadata"],
      tools: ["read_file"],
    },
  },
  enrich_visuals: {
    title: "Convert source figures",
    subtitle:
      "Transcribes selected source figures with the configured vision model and replaces only supported, high-confidence conversions in the processed Markdown.",
    kind: "Vision extraction + deterministic Markdown serialization",
    actions: [
      "Reads source figure crops from the immutable Docling extraction and uses nearby repaired page text as context.",
      "Converts diagrams and timelines to Mermaid, tables to GFM, supported charts and coordinate-explicit maps to constrained Vega-Lite, equations to KaTeX-compatible LaTeX, legible code to fenced blocks, and text figures to Markdown prose.",
      "Keeps the original image in the figure gallery and leaves the inline image unchanged whenever the source is incomplete or uncertain.",
      "Persists each successful figure response so an interrupted run can resume without repeating completed vision calls.",
    ],
    reads: ["selected repaired pages", "source figure PNGs", "section paths"],
    writes: ["visually enriched Markdown", "figure conversion provenance"],
    llm: "Visual extractor, one schema-constrained vision call per eligible figure using the configured vision model.",
    resources: {
      agents: ["visual_extractor"],
      skills: ["transcribe-source-figure"],
      tools: ["read_file"],
    },
  },
  classify_content: {
    title: "Select Distill pages",
    subtitle:
      "Classifies audited source pages before page repairs and keeps uncertain pages in the working set.",
    kind: "LLM classification + conservative policy",
    actions: [
      "Reads each original page with its book title and section path.",
      "Excludes only high-confidence navigation, legal and noise pages; blank pages stay excluded and image-only pages stay included for review.",
      "Writes a versioned selection used to skip repairs and build the processed book that Distill pins.",
    ],
    reads: ["audited source pages", "page assessments", "book title", "section paths"],
    writes: ["versioned Distill page selection"],
    llm: "Content classifier agent, one bounded page at a time; obvious printed contents pages are excluded by a deterministic check.",
    resources: { agents: ["content_classifier"], skills: [], tools: [] },
  },
  materialize_processed_book: {
    title: "Build processed pages",
    subtitle:
      "Creates a dense page map from selected, nonblank pages while retaining each original source page.",
    kind: "deterministic materialization",
    actions: [
      "Keeps pages selected for Distill and omits pages classified as blank.",
      "Assigns contiguous processed page numbers in source order.",
      "Checks normalized page content against its stored hash and gathers headings for the generated contents tree.",
    ],
    reads: ["versioned page selection", "AI-processed pages", "retained-page headings", "section paths"],
    writes: ["immutable processed-page candidate"],
    llm: "No model call.",
    resources: noResources,
  },
  reconcile_contents: {
    title: "Generate contents",
    subtitle: "Builds a standardized contents tree from headings in the retained pages.",
    kind: "deterministic contents generation",
    actions: [
      "Builds the table of contents from headings and section paths on retained pages.",
      "Creates a stable hierarchy and links each entry to its processed page number.",
      "Does not use the book's printed table of contents as an input or fallback.",
    ],
    reads: ["retained-page headings", "section paths", "processed-page candidate"],
    writes: ["generated contents tree", "processed-page links"],
    llm: "No model call.",
    resources: noResources,
  },
  publish: {
    title: "Publish results",
    subtitle: "Writes the quality report and, last of all, the run manifest.",
    kind: "deterministic",
    actions: [
      "Counts delivered, blank, image-only, review and unverified pages.",
      "Marks the run complete only when every page is accepted and the metadata needs no review.",
      "Writes the manifest after every other artifact, so a finished run is always whole.",
    ],
    reads: ["AI-processed pages", "book metadata"],
    writes: ["quality report", "run manifest"],
    llm: "No model call.",
    resources: noResources,
  },
};

/** The run's own totals, shown on the node that publishes them. */
export type AgenticRunSummary = {
  deliveredPages: number;
  reviewPages: number;
  unverifiedPages: number;
};

export const EMPTY_AGENTIC_PROGRESS: AgenticProgress = {
  current_node: null,
  node_status: {},
  audited_pages: [],
  audit_warning_pages: [],
  processed_pages: [],
  repaired_pages: [],
  active_audit_pages: [],
  active_repair_pages: [],
  classified_pages: [],
  active_classification_pages: [],
  selected_page_count: 0,
  excluded_page_count: 0,
  visual_candidate_count: 0,
  visual_completed_count: 0,
  visual_converted_count: 0,
  visual_failed_count: 0,
};

function pageSelectionCounts(progress: AgenticProgress, total?: number) {
  const selected = progress.selected_page_count;
  const excluded = progress.excluded_page_count;
  if (
    selected !== undefined &&
    excluded !== undefined &&
    (total === undefined ? selected + excluded > 0 : selected + excluded === total)
  ) {
    return { selected, excluded, known: true };
  }
  const excludedCount = progress.excluded_page_count ?? 0;
  const fallbackTotal = total ?? progress.processed_pages.length;
  return {
    selected: Math.max(0, fallbackTotal - excludedCount),
    excluded: excludedCount,
    known: false,
  };
}

function retainedVisualCount(progress: AgenticProgress): number {
  return Math.max(
    0,
    (progress.visual_completed_count ?? 0) -
      (progress.visual_converted_count ?? 0) -
      (progress.visual_failed_count ?? 0),
  );
}

function repairedSelectionCount(
  progress: AgenticProgress,
  status: AgenticNodeStatus,
  total?: number,
): number {
  const { selected } = pageSelectionCounts(progress, total);
  if (status === "done") return selected;
  return Math.min(progress.repaired_pages?.length ?? 0, selected);
}

export type ChapterProgress = ChapterRange & { processed: number; total: number };

export function chapterProgress(
  chapters: readonly ChapterRange[],
  processedPages: readonly number[],
): ChapterProgress[] {
  const processed = new Set(processedPages);
  return chapters.flatMap((chapter) => {
    const total = Math.max(0, chapter.last_page - chapter.first_page + 1);
    if (total === 0) return [];
    let count = 0;
    for (let page = chapter.first_page; page <= chapter.last_page; page += 1) {
      if (processed.has(page)) count += 1;
    }
    return [{ ...chapter, processed: count, total }];
  });
}

export function chapterProgressSummary(
  chapters: readonly ChapterRange[],
  processedPages: readonly number[],
): { completed: number; total: number } | null {
  const values = chapterProgress(chapters, processedPages);
  if (values.length === 0) return null;
  return {
    completed: values.filter((chapter) => chapter.processed === chapter.total).length,
    total: values.length,
  };
}

export function agenticNodeStatus(
  progress: AgenticProgress,
  nodeId: string,
  runStatus: string,
): AgenticNodeStatus {
  const status = progress.node_status[nodeId];
  if (status) return status;
  if (runStatus === "failed" && progress.current_node === nodeId) return "failed";
  return "pending";
}

export function agenticPercent(completed: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
}

export function agenticNodeCaption(
  progress: AgenticProgress,
  nodeId: string,
  total: number,
  unitPlural = "pages",
  status?: AgenticNodeStatus,
): string | null {
  if (total <= 0) return null;
  if (nodeId === "enrich_visuals") {
    const candidates = progress.visual_candidate_count ?? 0;
    const completed = progress.visual_completed_count ?? 0;
    const converted = progress.visual_converted_count ?? 0;
    const failed = progress.visual_failed_count ?? 0;
    return candidates > 0
      ? [
          `${completed} of ${candidates} processed`,
          `${converted} converted`,
          `${retainedVisualCount(progress)} retained`,
          `${failed} failed`,
        ].join(" · ")
      : "No eligible figures on selected pages";
  }
  if (nodeId === "audit_batch") {
    const done = progress.audited_pages.length;
    const active = progress.active_audit_pages.length;
    return active > 0
      ? `${done} ${unitPlural} audited · ${active} active · ${total} total`
      : `${done} of ${total} ${unitPlural} audited`;
  }
  if (nodeId === "repair_pages") {
    const done = repairedSelectionCount(progress, status ?? "running", total);
    const active = progress.active_repair_pages.length;
    const { selected, excluded } = pageSelectionCounts(progress, total);
    return active > 0
      ? `${done} of ${selected} selected ${unitPlural} processed · ${excluded} excluded from repair · ${active} active`
      : `${done} of ${selected} selected ${unitPlural} processed · ${excluded} excluded from repair`;
  }
  if (nodeId === "classify_content") {
    const done = progress.classified_pages?.length ?? 0;
    const active = progress.active_classification_pages?.length ?? 0;
    const { selected, excluded, known } = pageSelectionCounts(progress, total);
    const selection = known ? ` · ${selected} selected · ${excluded} excluded` : "";
    return active > 0
      ? `${done} ${unitPlural} classified · ${active} active · ${total} total${selection}`
      : `${done} of ${total} ${unitPlural} classified${selection}`;
  }
  return null;
}

/** A finished node's headline number ("275 pages audited"), as the stage graphs show it under the label. */
export function agenticNodeKpi(
  progress: AgenticProgress,
  nodeId: string,
  status: AgenticNodeStatus,
  unitPlural: string,
  summary: AgenticRunSummary | null,
  total?: number,
): string | null {
  if (status !== "done") return null;
  if (nodeId === "enrich_visuals") {
    const converted = progress.visual_converted_count ?? 0;
    const candidates = progress.visual_candidate_count ?? 0;
    const failed = progress.visual_failed_count ?? 0;
    return [
      `${converted} converted`,
      `${retainedVisualCount(progress)} retained`,
      `${failed} failed`,
      `${candidates} figures`,
    ].join(" · ");
  }
  if (nodeId === "audit_batch") return `${progress.audited_pages.length} ${unitPlural} audited`;
  if (nodeId === "repair_pages") {
    const { excluded } = pageSelectionCounts(progress, total);
    return `${repairedSelectionCount(progress, status, total)} selected processed · ${excluded} excluded from repair`;
  }
  if (nodeId === "classify_content") {
    const { selected, excluded } = pageSelectionCounts(progress, total);
    return `${selected} selected · ${excluded} excluded from Distill`;
  }
  if (nodeId === "publish" && summary) {
    return `${summary.deliveredPages} delivered · ${summary.reviewPages} need review · ${summary.unverifiedPages} audit unverified`;
  }
  return null;
}

/** The line a running node shows in monospace: how far it is and how many pages it has in flight. */
export function agenticNodeLiveLine(
  progress: AgenticProgress,
  nodeId: string,
  status: AgenticNodeStatus,
  total: number,
): string | null {
  if (status !== "running" || total <= 0) return null;
  if (nodeId === "enrich_visuals") {
    return [
      `${progress.visual_completed_count ?? 0}/${progress.visual_candidate_count ?? 0} processed`,
      `${progress.visual_converted_count ?? 0} converted`,
      `${retainedVisualCount(progress)} retained`,
      `${progress.visual_failed_count ?? 0} failed`,
    ].join(" · ");
  }
  const [done, active] =
    nodeId === "audit_batch"
      ? [progress.audited_pages.length, progress.active_audit_pages.length]
      : nodeId === "repair_pages"
        ? [repairedSelectionCount(progress, status, total), progress.active_repair_pages.length]
        : nodeId === "classify_content"
          ? [progress.classified_pages?.length ?? 0, progress.active_classification_pages?.length ?? 0]
          : [null, 0];
  if (done === null) return null;
  if (nodeId === "repair_pages") {
    const { selected, excluded } = pageSelectionCounts(progress, total);
    return active > 0
      ? `${done}/${selected} selected · ${excluded} excluded · ${active} active`
      : `${done}/${selected} selected · ${excluded} excluded`;
  }
  return active > 0 ? `${done}/${total} · ${active} in flight` : `${done}/${total}`;
}

/** A node as the graph and its list draw it. */
export type AgenticNodeView = {
  id: string;
  label: string;
  status: AgenticNodeStatus;
  statusLabel?: string;
  kpi: string | null;
  liveLine: string | null;
  resources: AgenticNodeResources;
  progress: AgenticNodeProgress | null;
  replayable?: boolean;
};

function agenticNodeProgress(
  progress: AgenticProgress,
  nodeId: AgenticNodeId,
  status: AgenticNodeStatus,
  total: number,
): AgenticNodeProgress | null {
  if (total <= 0 || status === "pending") return null;
  const pageProgress = progress.node_page_progress?.[nodeId];
  if (pageProgress && pageProgress.total_count > 0) {
    return {
      current: Math.min(pageProgress.completed_count, pageProgress.total_count),
      total: pageProgress.total_count,
      label:
        nodeId === "materialize_processed_book"
          ? "pages materialized"
          : nodeId === "reconcile_contents"
            ? "page headings scanned"
            : nodeId === "plan_repairs"
              ? "pages planned"
              : "pages verified",
      active: pageProgress.active_count,
    };
  }
  if (nodeId === "enrich_visuals") {
    const visualTotal = progress.visual_candidate_count ?? 0;
    if (visualTotal <= 0) return null;
    return {
      current: Math.min(progress.visual_completed_count ?? 0, visualTotal),
      total: visualTotal,
      label: "figures processed",
      active: 0,
    };
  }
  if (nodeId === "audit_batch") {
    return {
      current: Math.min(progress.audited_pages.length, total),
      total,
      label: "pages audited",
      active: progress.active_audit_pages.length,
    };
  }
  if (nodeId === "repair_pages") {
    const { selected } = pageSelectionCounts(progress, total);
    if (selected <= 0) return null;
    return {
      current: repairedSelectionCount(progress, status, total),
      total: selected,
      label: "selected pages processed",
      active: progress.active_repair_pages.length,
    };
  }
  if (nodeId === "classify_content") {
    return {
      current: Math.min(progress.classified_pages?.length ?? 0, total),
      total,
      label: "pages classified",
      active: progress.active_classification_pages?.length ?? 0,
    };
  }
  return null;
}

export function agenticNodeViews(
  progress: AgenticProgress,
  runStatus: string,
  total: number,
  unitPlural: string,
  summary: AgenticRunSummary | null,
): AgenticNodeView[] {
  return AGENTIC_NODES.map((node) => {
    const status = agenticNodeStatus(progress, node.id, runStatus);
    return {
      id: node.id,
      label: node.label,
      status,
      kpi: agenticNodeKpi(progress, node.id, status, unitPlural, summary, total),
      liveLine: agenticNodeLiveLine(progress, node.id, status, total),
      resources: AGENTIC_NODE_DETAILS[node.id].resources,
      progress: agenticNodeProgress(progress, node.id, status, total),
    };
  });
}

/** One line above the graph: the run, then how many of the nodes have finished. */
export function agenticBoardCaption(
  progress: AgenticProgress,
  runStatus: string,
  hasRun: boolean,
  processed: number,
  total: number,
  unitPlural: string,
): string {
  const done = AGENTIC_NODES.filter(
    (node) => agenticNodeStatus(progress, node.id, runStatus) === "done",
  ).length;
  const nodes = `${done} of ${AGENTIC_NODES.length} nodes done`;
  return hasRun
    ? `Run ${runStatus} · ${processed} of ${total} ${unitPlural} processed · ${nodes}`
    : `Ready to run after extraction · ${nodes}`;
}
