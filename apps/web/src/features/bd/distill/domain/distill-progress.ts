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
    detail: "Find the book’s central claims, methods, evidence, and useful examples in each learning unit.",
    reads: ["Learning units", "Source page references"],
    writes: ["Candidate lessons", "Evidence quotes"],
    llm: "Learning Extractor for each new unit; Evidence Verifier when supported lesson candidates exist.",
    resources: {
      agents: ["learning_extractor", "evidence_verifier"],
      skills: ["learning-extractor", "evidence-verifier"],
      tools: ["read_file"],
    },
  },
  {
    id: "prepare_chapters",
    label: "Map chapters",
    detail: "Group source-backed lessons by chapter and topic while preserving their source locations.",
    reads: ["Candidate lessons", "Book chapter outline"],
    writes: ["Chapter lesson groups"],
    llm: "No LLM calls. This stage deterministically groups persisted, source-backed lessons.",
    resources: { agents: [], skills: [], tools: [] },
  },
  {
    id: "synthesize_chapter",
    label: "Explain chapters",
    detail: "Turn each chapter’s supported lessons into a concise explanation and chapter-level guide.",
    reads: ["Chapter lesson groups", "Evidence quotes"],
    writes: ["Chapter explanations", "Chapter-level learning models"],
    llm: "Chapter Synthesizer; Visual Artifact Builder when a chapter needs a structured visual.",
    resources: {
      agents: ["chapter_synthesizer", "visual_artifact_builder"],
      skills: ["chapter-synthesizer", "visual-artifact-builder"],
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
    llm: "Book Synthesizer and Decision Brief Writer; Research Analyst when research is enabled.",
    resources: {
      agents: ["book_synthesizer", "decision_brief_writer", "research_analyst"],
      skills: ["book-synthesizer", "decision-brief-writer", "research-analyst"],
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

export const DISTILL_USAGE_COUNTERS: Partial<Record<DistillNode["id"], readonly string[]>> = {
  extract_unit: ["learning_extractor", "evidence_verifier"],
  synthesize_chapter: ["chapter_synthesizer", "visual_artifact_builder"],
  synthesize_book: ["book_synthesizer", "decision_brief_writer", "research_analyst"],
  publish_report: ["report_reviewer"],
};

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

function nodeProgress(
  nodeId: DistillNode["id"],
  status: AgenticNodeStatus,
  progress: DistillRun["progress"] | null,
): AgenticNodeProgress | null {
  if (progress === null || status === "pending") return null;
  if (nodeId === "extract_unit" && progress.total_units > 0) {
    return {
      current: Math.min(progress.completed_units, progress.total_units),
      total: progress.total_units,
      label: "units extracted",
      active: 0,
    };
  }
  if (nodeId === "synthesize_chapter" && progress.total_chapters > 0) {
    return {
      current: Math.min(progress.completed_chapters, progress.total_chapters),
      total: progress.total_chapters,
      label: "chapters explained",
      active: 0,
    };
  }
  return null;
}

export function distillNodeStatus(
  nodeId: string,
  status: DistillRun["status"] | null,
  progress: DistillRun["progress"] | null,
): AgenticNodeStatus {
  if (status === "done") return "done";

  const recorded = progress?.node_status[nodeId];
  if (recorded === "done") return "done";
  if (recorded === "failed") return "failed";

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
  if (status === "done") return `${DISTILL_NODES.length} of ${DISTILL_NODES.length} stages complete.`;
  if (status === "cancelled") return "Run stopped before the Learning Guide was published.";
  if (status === "failed") {
    const failed = DISTILL_NODES.find((node) => distillNodeStatus(node.id, status, progress) === "failed");
    return failed ? `Run failed during ${failed.label.toLowerCase()}.` : "Run failed before completion.";
  }
  const current = DISTILL_NODES.find((node) => node.id === progress?.current_node);
  return current ? `Working on ${current.label.toLowerCase()}.` : "Distill is processing the book.";
}

function nodeKpi(
  nodeId: string,
  status: AgenticNodeStatus,
  progress: DistillRun["progress"] | null,
): string | null {
  if (status !== "done" || progress === null) return null;
  if (nodeId === "extract_unit" && progress.total_units > 0) {
    return `${progress.completed_units.toLocaleString()} / ${progress.total_units.toLocaleString()} units`;
  }
  if (nodeId === "synthesize_chapter" && progress.total_chapters > 0) {
    return `${progress.completed_chapters.toLocaleString()} / ${progress.total_chapters.toLocaleString()} chapters`;
  }
  return null;
}

function nodeLiveLine(
  nodeId: string,
  status: DistillRun["status"] | null,
  progress: DistillRun["progress"] | null,
): string | null {
  if (status !== "running" || progress === null || progress.current_node !== nodeId) return null;
  if (nodeId === "extract_unit" && progress.total_units > 0) {
    return `${progress.completed_units.toLocaleString()}/${progress.total_units.toLocaleString()} units`;
  }
  if ((nodeId === "prepare_chapters" || nodeId === "synthesize_chapter") && progress.total_chapters > 0) {
    return `${progress.completed_chapters.toLocaleString()}/${progress.total_chapters.toLocaleString()} chapters`;
  }
  return null;
}
