import type { AgenticProgress, ChapterRange } from "../entities";

export const AGENTIC_NODES = [
  { id: "audit_batch", label: "Audit pages" },
  { id: "plan_repairs", label: "Plan repairs" },
  { id: "repair_pages", label: "Repair pages" },
  { id: "normalize_metadata", label: "Normalize metadata" },
  { id: "classify_content", label: "Select Distill pages" },
  { id: "publish", label: "Publish results" },
] as const;

export const AGENTIC_LANGGRAPH_TOPOLOGY = `flowchart TD
  __start__([__start__]) --> audit_batch["audit_batch"]
  audit_batch --> plan_repairs["plan_repairs"]
  plan_repairs --> repair_pages["repair_pages"]
  repair_pages --> normalize_metadata["normalize_metadata"]
  normalize_metadata --> classify_content["classify_content"]
  classify_content --> publish["publish"]
  publish --> __end__([__end__])`;

export type AgenticNodeId = (typeof AGENTIC_NODES)[number]["id"];
export type AgenticNodeStatus = "pending" | "running" | "done" | "failed";

/** What each node does, in plain words (the sheet's Overview), and what it reads, writes and asks of a model. */
export type AgenticNodeDetails = {
  title: string;
  subtitle: string;
  kind: string;
  actions: readonly string[];
  reads: readonly string[];
  writes: readonly string[];
  llm: string;
};

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
  },
  plan_repairs: {
    title: "Plan repairs",
    subtitle: "Routes every finding to the specialist that handles its issue type.",
    kind: "deterministic routing",
    actions: [
      "Sends split words, fused words and corrupted text to the text specialist.",
      "Sends layout, contents and table findings to the structure specialist.",
      "Leaves control characters and soft hyphens to code, which removes them without a model.",
    ],
    reads: ["page assessments"],
    writes: ["repair work items"],
    llm: "No model call. The routing is a fixed table.",
  },
  repair_pages: {
    title: "Repair pages",
    subtitle: "Specialist agents propose minimal patches; only provably safe ones are applied.",
    kind: "LLM specialists + safe patching",
    actions: [
      "Removes control characters and soft hyphens, and tidies table padding, in code.",
      "Applies a model patch on its own only when it changes spaces and nothing else, and never inside a URL, path or address.",
      "Drops patches that add words, links or markers the page never had; everything else stays a suggestion for review.",
    ],
    reads: ["extracted pages", "page assessments"],
    writes: ["AI-processed pages", "patch proposals"],
    llm: "Text-repair and structure-repair agents, called only for the findings assigned to them.",
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
  },
  classify_content: {
    title: "Select Distill pages",
    subtitle:
      "Classifies each normalized page's role in the book and keeps uncertain pages in the working set.",
    kind: "LLM classification + conservative policy",
    actions: [
      "Reads each repaired page with its book title and section path.",
      "Excludes only high-confidence navigation, legal and noise pages; blank pages stay excluded and image-only pages stay included for review.",
      "Writes a versioned selection that Distill pins before creating learning units.",
    ],
    reads: ["AI-processed pages", "book metadata", "book outline"],
    writes: ["versioned Distill page selection"],
    llm: "Content classifier agent, one bounded page at a time; uncertain or failed classifications are kept.",
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
  active_audit_pages: [],
  active_repair_pages: [],
  classified_pages: [],
  active_classification_pages: [],
};

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
): string | null {
  if (total <= 0) return null;
  if (nodeId === "audit_batch") {
    const done = progress.audited_pages.length;
    const active = progress.active_audit_pages.length;
    return active > 0
      ? `${done} ${unitPlural} audited · ${active} active · ${total} total`
      : `${done} of ${total} ${unitPlural} audited`;
  }
  if (nodeId === "repair_pages") {
    const done = progress.processed_pages.length;
    const active = progress.active_repair_pages.length;
    return active > 0
      ? `${done} ${unitPlural} repaired · ${active} active · ${total} total`
      : `${done} of ${total} ${unitPlural} repaired`;
  }
  if (nodeId === "classify_content") {
    const done = progress.classified_pages?.length ?? 0;
    const active = progress.active_classification_pages?.length ?? 0;
    return active > 0
      ? `${done} ${unitPlural} classified · ${active} active · ${total} total`
      : `${done} of ${total} ${unitPlural} classified`;
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
): string | null {
  if (status !== "done") return null;
  if (nodeId === "audit_batch") return `${progress.audited_pages.length} ${unitPlural} audited`;
  if (nodeId === "repair_pages") return `${progress.processed_pages.length} ${unitPlural} repaired`;
  if (nodeId === "classify_content")
    return `${progress.classified_pages?.length ?? 0} ${unitPlural} classified`;
  if (nodeId === "publish" && summary) {
    return `${summary.deliveredPages} delivered · ${summary.reviewPages} need review`;
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
  const [done, active] =
    nodeId === "audit_batch"
      ? [progress.audited_pages.length, progress.active_audit_pages.length]
      : nodeId === "repair_pages"
        ? [progress.processed_pages.length, progress.active_repair_pages.length]
        : nodeId === "classify_content"
          ? [progress.classified_pages?.length ?? 0, progress.active_classification_pages?.length ?? 0]
          : [null, 0];
  if (done === null) return null;
  return active > 0 ? `${done}/${total} · ${active} in flight` : `${done}/${total}`;
}

/** A node as the graph and its list draw it. */
export type AgenticNodeView = {
  id: string;
  label: string;
  status: AgenticNodeStatus;
  kpi: string | null;
  liveLine: string | null;
};

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
      kpi: agenticNodeKpi(progress, node.id, status, unitPlural, summary),
      liveLine: agenticNodeLiveLine(progress, node.id, status, total),
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
