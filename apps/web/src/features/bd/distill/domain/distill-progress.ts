import type { AgenticNodeProgress, AgenticNodeStatus, AgenticNodeView } from "../../domain/agentic-progress";
import type { DistillRun } from "../entities";

export const DISTILL_NODES = [
  {
    id: "prepare_units",
    label: "Prepare the book",
    detail: "Create source-linked learning units from the extracted book pages.",
    reads: ["Extracted pages", "Ingestion quality result"],
    writes: ["Learning units with page references"],
    llm: "No LLM calls. This stage deterministically prepares and chunks the selected book pages.",
    resources: { agents: [], skills: [], tools: [] },
  },
  {
    id: "extract_unit",
    label: "Extract lessons",
    detail:
      "Find source-supported claims, methods, evidence, and examples; try one bounded repair when a quotation does not resolve to the page.",
    reads: ["Learning units", "Source page references"],
    writes: ["Candidate lessons", "Exact evidence quotes"],
    llm: "Learning Extractor for each new unit.",
    resources: {
      agents: ["learning_extractor"],
      skills: ["learning-extractor"],
      tools: ["read_file"],
    },
  },
  {
    id: "verify_unit",
    label: "Verify evidence",
    detail:
      "Check whether each source passage supports its proposed lesson; retain uncertain lessons as unverified for review.",
    reads: ["Candidate lessons", "Exact evidence quotes"],
    writes: ["Verified lessons", "Evidence review flags"],
    llm: "Evidence Verifier for each unit with supported lesson candidates.",
    resources: {
      agents: ["evidence_verifier"],
      skills: ["evidence-verifier"],
      tools: ["read_file"],
    },
  },
  {
    id: "prepare_chapters",
    label: "Map chapters",
    detail:
      "Map chapter boundaries from processed contents, ignore front-matter headings, and flag empty or uncertain source sections.",
    reads: ["Verified lessons", "Book chapter outline"],
    writes: ["Chapter lesson groups"],
    llm: "No LLM calls. This stage deterministically groups persisted, source-backed lessons.",
    resources: { agents: [], skills: [], tools: [] },
  },
  {
    id: "synthesize_chapter",
    label: "Explain chapters",
    detail: "Turn each chapter’s supported lessons into a concise explanation and chapter-level guide.",
    reads: ["Chapter lesson groups", "Evidence quotes"],
    writes: ["Chapter explanations", "Text-first chapter guides"],
    llm: "Chapter Synthesizer for each chapter.",
    resources: {
      agents: ["chapter_synthesizer"],
      skills: ["chapter-synthesizer"],
      tools: ["read_file"],
    },
  },
  {
    id: "review_chapter_coverage",
    label: "Review chapter coverage",
    detail:
      "Check each chapter against its source lessons, apply up to two evidence-grounded corrections, and re-review until it converges or needs manual review.",
    reads: ["Chapter guides", "Verified lessons", "Learning objectives"],
    writes: ["Chapter corrections", "Final coverage review and unresolved gaps"],
    llm: "Report Reviewer audits each chapter; Chapter Synthesizer makes bounded corrections when needed.",
    resources: {
      agents: ["report_reviewer", "chapter_synthesizer"],
      skills: ["report-reviewer", "chapter-synthesizer"],
      tools: ["read_file"],
    },
  },
  {
    id: "build_chapter_visuals",
    label: "Build chapter visuals",
    detail: "Build and validate diagrams, equations, charts, timelines, and maps from saved chapter guides.",
    reads: ["Saved chapter guides", "Source evidence"],
    writes: ["Validated chapter visuals"],
    llm: "Visual Artifact Builder for each chapter; unsupported visual items are omitted.",
    resources: {
      agents: ["visual_artifact_builder"],
      skills: ["visual-artifact-builder"],
      tools: ["read_file"],
    },
  },
  {
    id: "synthesize_book",
    label: "Connect the book",
    detail:
      "Connect chapter findings into the book’s thesis, key takeaways, and cross-chapter relationships.",
    reads: ["Chapter explanations", "Verified lessons"],
    writes: ["Central thesis", "Key takeaways", "Cross-chapter connections"],
    llm: "Book Synthesizer; its hierarchical batches are reported as they run.",
    resources: {
      agents: ["book_synthesizer"],
      skills: ["book-synthesizer"],
      tools: ["read_file"],
    },
  },
  {
    id: "write_decision_brief",
    label: "Write decision brief",
    detail: "Create a separate decision brief with recommendations grounded in the book’s verified lessons.",
    reads: ["Whole-book synthesis", "Verified lessons"],
    writes: ["Source-cited decision brief"],
    llm: "Decision Brief Writer when cited evidence supports a brief.",
    resources: {
      agents: ["decision_brief_writer"],
      skills: ["decision-brief-writer"],
      tools: ["read_file"],
    },
  },
  {
    id: "research_context",
    label: "Add current context",
    detail: "Gather dated external context when enabled, kept separate from claims about the book.",
    reads: ["Whole-book synthesis", "Configured search connection"],
    writes: ["Separately attributed research appendix"],
    llm: "Research Analyst when research is enabled.",
    resources: {
      agents: ["research_analyst"],
      skills: ["research-analyst"],
      tools: ["read_file", "Search MCP when configured"],
    },
  },
  {
    id: "publish_report",
    label: "Publish the guide",
    detail: "Check source coverage and citation integrity, then render the Learning Guide and its visuals.",
    reads: ["Synthesized book and chapters", "Source references"],
    writes: ["Learning Guide", "Coverage and citation checks"],
    llm: "Report Reviewer checks the assembled Learning Guide before publication.",
    resources: { agents: ["report_reviewer"], skills: ["report-reviewer"], tools: ["read_file"] },
  },
] as const;

export type DistillNode = (typeof DISTILL_NODES)[number];

export const DISTILL_REPORT_PIPELINE_NODE_IDS = [
  "synthesize_book",
  "research_context",
  "write_decision_brief",
] as const;

/** Top-level nodes registered in the compiled Distill LangGraph. */
export const DISTILL_LANGGRAPH_NODES = [
  { id: "prepare_units", kind: "stage" },
  { id: "extract_unit", kind: "stage" },
  { id: "verify_unit", kind: "stage" },
  { id: "prepare_chapters", kind: "stage" },
  { id: "synthesize_chapter", kind: "stage" },
  { id: "review_chapter_coverage", kind: "stage" },
  {
    id: "dispatch_parallel_stages",
    kind: "control",
    label: "Dispatch parallel stages",
    detail:
      "After chapter coverage review, choose the chapter-visual and report branches. Both branches can run in parallel.",
  },
  { id: "build_chapter_visuals", kind: "stage" },
  {
    id: "report_pipeline",
    kind: "subgraph",
    label: "Report pipeline",
    detail:
      "This compiled subgraph runs book synthesis, current-context research, and the decision brief in sequence. Open it to inspect its internal LangGraph.",
  },
  { id: "publish_report", kind: "stage" },
] as const;

export type DistillLangGraphNode = (typeof DISTILL_LANGGRAPH_NODES)[number];

export const DISTILL_CONNECTIONS = [
  { source: "prepare_units", target: "extract_unit" },
  {
    source: "prepare_units",
    target: "prepare_chapters",
    sourceHandle: "source-right",
    targetHandle: "target-right",
  },
  { source: "extract_unit", target: "verify_unit" },
  {
    source: "extract_unit",
    target: "prepare_chapters",
    sourceHandle: "source-right",
    targetHandle: "target-right",
  },
  { source: "verify_unit", target: "prepare_chapters" },
  { source: "prepare_chapters", target: "synthesize_chapter" },
  { source: "synthesize_chapter", target: "review_chapter_coverage" },
  { source: "review_chapter_coverage", target: "dispatch_parallel_stages" },
  { source: "dispatch_parallel_stages", target: "build_chapter_visuals" },
  { source: "dispatch_parallel_stages", target: "report_pipeline" },
  { source: "build_chapter_visuals", target: "publish_report" },
  { source: "report_pipeline", target: "publish_report" },
] as const;

export const DISTILL_REPORT_PIPELINE_CONNECTIONS = [
  { source: "synthesize_book", target: "research_context" },
  { source: "research_context", target: "write_decision_brief" },
] as const;

export const DISTILL_POSITIONS = {
  prepare_units: { x: 284, y: 16 },
  extract_unit: { x: 284, y: 182 },
  verify_unit: { x: 284, y: 348 },
  prepare_chapters: { x: 284, y: 514 },
  synthesize_chapter: { x: 284, y: 680 },
  review_chapter_coverage: { x: 284, y: 846 },
  dispatch_parallel_stages: { x: 284, y: 1012 },
  build_chapter_visuals: { x: 16, y: 1178 },
  report_pipeline: { x: 552, y: 1178 },
  publish_report: { x: 284, y: 1344 },
} as const;

export const DISTILL_REPORT_PIPELINE_POSITIONS = {
  synthesize_book: { x: 284, y: 16 },
  research_context: { x: 284, y: 182 },
  write_decision_brief: { x: 284, y: 348 },
} as const;

export const DISTILL_USAGE_COUNTERS: Partial<Record<DistillNode["id"], readonly string[]>> = {
  extract_unit: ["learning_extractor"],
  verify_unit: ["evidence_verifier"],
  synthesize_chapter: ["chapter_synthesizer"],
  review_chapter_coverage: ["report_reviewer", "chapter_coverage_corrector"],
  build_chapter_visuals: ["visual_artifact_builder"],
  synthesize_book: ["book_synthesizer"],
  write_decision_brief: ["decision_brief_writer"],
  research_context: ["research_analyst"],
  publish_report: ["report_reviewer"],
};

/** Usage attributed to the report subgraph is the sum of its three contained stages. */
export const DISTILL_REPORT_PIPELINE_USAGE_COUNTERS: readonly string[] =
  DISTILL_REPORT_PIPELINE_NODE_IDS.flatMap((nodeId) => DISTILL_USAGE_COUNTERS[nodeId] ?? []);

/** The dispatcher has no model call of its own; these are the branches it schedules. */
export const DISTILL_DISPATCH_USAGE_COUNTERS: readonly string[] = [
  "visual_artifact_builder",
  ...DISTILL_REPORT_PIPELINE_USAGE_COUNTERS,
];

export function distillNodeViews(
  status: DistillRun["status"] | null,
  progress: DistillRun["progress"] | null,
): AgenticNodeView[] {
  return DISTILL_NODES.map((node) => {
    const nodeStatus = distillNodeStatus(node.id, status, progress);
    return {
      id: node.id,
      label: node.label,
      status: nodeStatus,
      kpi: nodeKpi(node.id, nodeStatus, progress),
      liveLine: nodeLiveLine(node.id, status, progress),
      resources: node.resources,
      progress: nodeProgress(node.id, nodeStatus, progress),
    };
  });
}

export function distillLangGraphNodeViews(
  status: DistillRun["status"] | null,
  progress: DistillRun["progress"] | null,
): AgenticNodeView[] {
  const stageViews = new Map(distillNodeViews(status, progress).map((view) => [view.id, view]));
  const reportStatuses = DISTILL_REPORT_PIPELINE_NODE_IDS.map((nodeId) =>
    distillNodeStatus(nodeId, status, progress),
  );
  const reportStatus = reportPipelineStatus(status, reportStatuses);
  const reportDone = reportStatuses.filter((nodeStatus) => nodeStatus === "done").length;
  const reportCurrentNode = DISTILL_NODES.find((node) => node.id === progress?.current_node);
  const visualStatus = distillNodeStatus("build_chapter_visuals", status, progress);
  const reviewStatus = distillNodeStatus("review_chapter_coverage", status, progress);
  const dispatchStarted = visualStatus !== "pending" || reportStatus !== "pending";
  const dispatchStatus = dispatchStarted
    ? "done"
    : reviewStatus === "done" && status === "running"
      ? "running"
      : reviewStatus === "done" && status === "failed"
        ? "failed"
        : reviewStatus === "done" && status === "done"
          ? "done"
          : "pending";

  return DISTILL_LANGGRAPH_NODES.map((node) => {
    if (node.kind === "stage") {
      const stageView = stageViews.get(node.id);
      return stageView ? { ...stageView, replayable: true } : emptyLangGraphNodeView(node.id, node.id);
    }
    if (node.kind === "control") {
      return {
        ...emptyLangGraphNodeView(node.id, node.label),
        status: dispatchStatus,
        liveLine: dispatchStatus === "running" ? "Choosing parallel branches" : null,
        replayable: true,
      };
    }

    return {
      ...emptyLangGraphNodeView(node.id, node.label),
      status: reportStatus,
      kpi: reportDone > 0 ? `${reportDone}/${DISTILL_REPORT_PIPELINE_NODE_IDS.length} report stages` : null,
      liveLine: reportStatus === "running" ? (reportCurrentNode?.label ?? "Report stages active") : null,
      replayable: true,
    };
  });
}

function emptyLangGraphNodeView(id: string, label: string): AgenticNodeView {
  return {
    id,
    label,
    status: "pending",
    kpi: null,
    liveLine: null,
    resources: { agents: [], skills: [], tools: [] },
    progress: null,
  };
}

function reportPipelineStatus(
  runStatus: DistillRun["status"] | null,
  nodeStatuses: readonly AgenticNodeStatus[],
): AgenticNodeStatus {
  if (nodeStatuses.includes("failed")) return "failed";
  if (nodeStatuses.every((nodeStatus) => nodeStatus === "done")) return "done";
  if (nodeStatuses.includes("running")) return "running";
  if (runStatus === "running" && nodeStatuses.includes("done")) return "running";
  return "pending";
}

function nodeProgress(
  nodeId: DistillNode["id"],
  status: AgenticNodeStatus,
  progress: DistillRun["progress"] | null,
): AgenticNodeProgress | null {
  if (progress === null || status === "pending") return null;
  const recorded = progress.node_progress?.[nodeId];
  if (recorded && recorded.total_count > 0) {
    return {
      current: Math.min(recorded.completed_count, recorded.total_count),
      total: recorded.total_count,
      label: nodeProgressLabel(nodeId),
      active: recorded.active_count,
    };
  }
  if (nodeId === "extract_unit" && progress.total_units > 0) {
    return {
      current: Math.min(progress.completed_extractions, progress.total_units),
      total: progress.total_units,
      label: "units extracted",
      active: 0,
    };
  }
  if (nodeId === "verify_unit" && progress.total_units > 0) {
    return {
      current: Math.min(progress.completed_units, progress.total_units),
      total: progress.total_units,
      label: "units checked",
      active: 0,
    };
  }
  if (nodeId === "synthesize_chapter" && progress.total_chapters > 0) {
    return {
      current: Math.min(progress.completed_chapter_guides, progress.total_chapters),
      total: progress.total_chapters,
      label: "chapter guides written",
      active: 0,
    };
  }
  if (nodeId === "build_chapter_visuals" && progress.total_chapters > 0) {
    return {
      current: Math.min(progress.completed_chapter_visuals, progress.total_chapters),
      total: progress.total_chapters,
      label: "visual stages complete",
      active: 0,
    };
  }
  return null;
}

function nodeProgressLabel(nodeId: string): string {
  switch (nodeId) {
    case "prepare_units":
      return "source pages prepared";
    case "extract_unit":
      return "units extracted";
    case "verify_unit":
      return "units checked";
    case "prepare_chapters":
      return "source units read";
    case "synthesize_chapter":
      return "chapter guides written";
    case "review_chapter_coverage":
      return "chapter review attempts complete";
    case "build_chapter_visuals":
      return "chapter visual stages complete";
    case "synthesize_book":
      return "synthesis batches complete";
    case "publish_report":
      return "source units assembled";
    case "write_decision_brief":
    case "research_context":
      return "items complete";
    default:
      return "items complete";
  }
}

function nodeProgressSummary(nodeId: string, progress: DistillRun["progress"]): string | null {
  const recorded = progress.node_progress?.[nodeId];
  if (!recorded || recorded.total_count <= 0) return null;
  return `${Math.min(recorded.completed_count, recorded.total_count).toLocaleString()}/${recorded.total_count.toLocaleString()} ${nodeProgressLabel(nodeId)}`;
}

export function distillNodeStatus(
  nodeId: string,
  status: DistillRun["status"] | null,
  progress: DistillRun["progress"] | null,
): AgenticNodeStatus {
  const recorded = progress?.node_status[nodeId];
  if (recorded === "done") return "done";
  if (recorded === "failed") return "failed";
  if (status === "done" && (!progress || Object.keys(progress.node_status).length === 0)) return "done";

  const current = progress?.current_node === nodeId;
  if (status === "failed") return current ? "failed" : "pending";
  if (status === "cancelled") return "pending";
  if (status === "running" && (recorded === "running" || current)) return "running";
  if (recorded === "running" && status !== "queued") return "running";
  return "pending";
}

export function distillBoardCaption(
  status: DistillRun["status"] | null,
  progress: DistillRun["progress"] | null,
): string {
  if (status === null) return "Select a stage to inspect its inputs and outputs.";
  if (status === "queued") return "Run queued · waiting for a worker.";
  if (status === "done") return "Learning Guide published.";
  if (status === "cancelled") return "Run stopped before the Learning Guide was published.";
  if (status === "failed") {
    const failed = DISTILL_NODES.find((node) => distillNodeStatus(node.id, status, progress) === "failed");
    return failed ? `Run failed during ${failed.label.toLowerCase()}.` : "Run failed before completion.";
  }
  const running = DISTILL_NODES.filter((node) => distillNodeStatus(node.id, status, progress) === "running");
  if (running.length > 1) {
    const labels = running.map((node) => node.label.toLowerCase()).join(", ");
    return `${running.length} stages are running in parallel: ${labels}.`;
  }
  const current = running[0] ?? DISTILL_NODES.find((node) => node.id === progress?.current_node);
  const task = current ? (progress?.active_tasks[current.id] ?? progress?.current_task) : null;
  if (current && task) return `Working on ${current.label.toLowerCase()} · ${task}.`;
  return current ? `Working on ${current.label.toLowerCase()}.` : "Distill is processing the book.";
}

function nodeKpi(
  nodeId: string,
  status: AgenticNodeStatus,
  progress: DistillRun["progress"] | null,
): string | null {
  if (status !== "done" || progress === null) return null;
  const recorded = nodeProgressSummary(nodeId, progress);
  if (recorded !== null) return recorded;
  if (nodeId === "extract_unit" && progress.total_units > 0) {
    return `${progress.completed_extractions.toLocaleString()} / ${progress.total_units.toLocaleString()} units`;
  }
  if (nodeId === "verify_unit" && progress.total_units > 0) {
    return `${progress.completed_units.toLocaleString()} / ${progress.total_units.toLocaleString()} units`;
  }
  if (nodeId === "synthesize_chapter" && progress.total_chapters > 0) {
    return `${progress.completed_chapter_guides.toLocaleString()} / ${progress.total_chapters.toLocaleString()} chapters`;
  }
  if (nodeId === "build_chapter_visuals" && progress.total_chapters > 0) {
    return `${progress.completed_chapter_visuals.toLocaleString()} / ${progress.total_chapters.toLocaleString()} stages`;
  }
  return null;
}

function nodeLiveLine(
  nodeId: string,
  status: DistillRun["status"] | null,
  progress: DistillRun["progress"] | null,
): string | null {
  const nodeIsRunning = progress?.node_status[nodeId] === "running" || progress?.current_node === nodeId;
  if (status !== "running" || progress === null || !nodeIsRunning) return null;
  const activeTask =
    progress.active_tasks[nodeId] ?? (progress.current_node === nodeId ? progress.current_task : null);
  if (activeTask) return activeTask;
  const recorded = nodeProgressSummary(nodeId, progress);
  if (recorded !== null) return recorded;
  if (nodeId === "extract_unit" && progress.total_units > 0) {
    return `${progress.completed_extractions.toLocaleString()}/${progress.total_units.toLocaleString()} units`;
  }
  if (nodeId === "verify_unit" && progress.total_units > 0) {
    return `${progress.completed_units.toLocaleString()}/${progress.total_units.toLocaleString()} units`;
  }
  if (nodeId === "synthesize_chapter" && progress.total_chapters > 0) {
    return `${progress.completed_chapter_guides.toLocaleString()}/${progress.total_chapters.toLocaleString()} chapter guides`;
  }
  if (nodeId === "build_chapter_visuals" && progress.total_chapters > 0) {
    return `${progress.completed_chapter_visuals.toLocaleString()}/${progress.total_chapters.toLocaleString()} visual stages`;
  }
  if (nodeId === "prepare_chapters" && progress.total_chapters > 0) {
    return `${progress.completed_chapters.toLocaleString()}/${progress.total_chapters.toLocaleString()} chapters`;
  }
  return null;
}
