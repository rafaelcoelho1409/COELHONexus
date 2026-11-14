import type { AgenticNodeProgress, AgenticNodeStatus, AgenticNodeView } from "../../domain/agentic-progress";
import type { DistillRun } from "../entities";

export const DISTILL_NODES = [
  {
    id: "prepare_units",
    label: "Prepare the book",
    detail: "Create source-linked learning units from the extracted book pages.",
    reads: ["Extracted pages", "Ingestion quality result"],
    writes: ["Learning units with page references"],
    resources: { agents: [], skills: [], tools: [] },
  },
  {
    id: "extract_unit",
    label: "Extract lessons",
    detail: "Find the book’s central claims, methods, evidence, and useful examples in each learning unit.",
    reads: ["Learning units", "Source page references"],
    writes: ["Candidate lessons", "Evidence quotes"],
    resources: { agents: ["learning_extractor", "evidence_verifier"], skills: [], tools: [] },
  },
  {
    id: "prepare_chapters",
    label: "Map chapters",
    detail: "Group source-backed lessons by chapter and topic while preserving their source locations.",
    reads: ["Candidate lessons", "Book chapter outline"],
    writes: ["Chapter lesson groups"],
    resources: { agents: [], skills: [], tools: [] },
  },
  {
    id: "synthesize_chapter",
    label: "Explain chapters",
    detail: "Turn each chapter’s supported lessons into a concise explanation and chapter-level guide.",
    reads: ["Chapter lesson groups", "Evidence quotes"],
    writes: ["Chapter explanations", "Chapter-level learning models"],
    resources: { agents: ["chapter_synthesizer"], skills: [], tools: [] },
  },
  {
    id: "synthesize_book",
    label: "Connect the book",
    detail:
      "Connect chapter findings into the book’s thesis, key takeaways, and cross-chapter relationships.",
    reads: ["Chapter explanations", "Verified lessons"],
    writes: ["Central thesis", "Key takeaways", "Cross-chapter connections"],
    resources: { agents: ["book_synthesizer"], skills: [], tools: [] },
  },
  {
    id: "publish_report",
    label: "Publish the guide",
    detail: "Check source coverage and citation integrity, then render the Learning Guide and its visuals.",
    reads: ["Synthesized book and chapters", "Source references"],
    writes: ["Learning Guide", "Coverage and citation checks"],
    resources: { agents: [], skills: [], tools: [] },
  },
] as const;

export const DISTILL_LANGGRAPH_TOPOLOGY = `flowchart TD
  __start__([__start__]) --> resume_route{"resume_route"}
  resume_route --> prepare_units["prepare_units"]
  resume_route --> prepare_chapters["prepare_chapters"]
  prepare_units --> extract_unit["extract_unit"]
  extract_unit --> prepare_chapters["prepare_chapters"]
  prepare_chapters --> synthesize_chapter["synthesize_chapter"]
  synthesize_chapter --> synthesize_book["synthesize_book"]
  synthesize_book --> publish_report["publish_report"]
  publish_report --> __end__([__end__])`;

export type DistillNode = (typeof DISTILL_NODES)[number];

export const DISTILL_USAGE_COUNTERS: Partial<Record<DistillNode["id"], readonly string[]>> = {
  extract_unit: ["learning_extractor", "evidence_verifier"],
  synthesize_chapter: ["chapter_synthesizer"],
  synthesize_book: ["book_synthesizer"],
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
