import type { AgenticNodeStatus, AgenticNodeView } from "../../domain/agentic-progress";
import type { DistillRun } from "../entities";

export const DISTILL_NODES = [
  {
    id: "prepare_units",
    label: "Prepare the book",
    detail: "Create source-linked learning units from the extracted book pages.",
    reads: ["Extracted pages", "Ingestion quality result"],
    writes: ["Learning units with page references"],
  },
  {
    id: "extract_unit",
    label: "Extract lessons",
    detail: "Find the book’s central claims, methods, evidence, and useful examples in each learning unit.",
    reads: ["Learning units", "Source page references"],
    writes: ["Candidate lessons", "Evidence quotes"],
  },
  {
    id: "prepare_chapters",
    label: "Map chapters",
    detail: "Group source-backed lessons by chapter and topic while preserving their source locations.",
    reads: ["Candidate lessons", "Book chapter outline"],
    writes: ["Chapter lesson groups"],
  },
  {
    id: "synthesize_chapter",
    label: "Explain chapters",
    detail: "Turn each chapter’s supported lessons into a concise explanation and chapter-level guide.",
    reads: ["Chapter lesson groups", "Evidence quotes"],
    writes: ["Chapter explanations", "Chapter-level learning models"],
  },
  {
    id: "synthesize_book",
    label: "Connect the book",
    detail:
      "Connect chapter findings into the book’s thesis, key takeaways, and cross-chapter relationships.",
    reads: ["Chapter explanations", "Verified lessons"],
    writes: ["Central thesis", "Key takeaways", "Cross-chapter connections"],
  },
  {
    id: "publish_report",
    label: "Publish the guide",
    detail: "Check source coverage and citation integrity, then render the Learning Guide and its visuals.",
    reads: ["Synthesized book and chapters", "Source references"],
    writes: ["Learning Guide", "Coverage and citation checks"],
  },
] as const;

export const DISTILL_LANGGRAPH_TOPOLOGY = `flowchart TD
  start([Start]) --> resume_route{"Resume target?"}
  resume_route -->|Fresh or extraction stage| prepare_units["Prepare the book"]
  resume_route -->|Chapter stage · restore saved units| prepare_chapters["Map chapters"]
  prepare_units --> extract_unit["Extract lessons"]
  extract_unit --> prepare_chapters["Map chapters"]
  prepare_chapters --> synthesize_chapter["Explain chapters · reuse saved outputs on resume"]
  synthesize_chapter --> synthesize_book["Connect the book"]
  synthesize_book --> publish_report["Publish the guide"]
  publish_report --> finish([End])`;

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
    };
  });
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
