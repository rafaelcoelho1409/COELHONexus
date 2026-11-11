"use client";

import {
  AgenticGraphPanel,
  type AgenticGraphStep,
  type AgenticStepStatus,
} from "@/shared/agentic-graph/agentic-graph-panel";
import type { AskTurn } from "../ask";

function stepStatus(graph: AskTurn["graph"], aliases: string[], selected: boolean): AgenticStepStatus {
  const hasVisited = aliases.some((node) => graph.visited_nodes.includes(node));
  const isActive = aliases.includes(graph.active_node ?? "");
  const lastVisited = graph.visited_nodes.at(-1);
  if (
    graph.status === "failed" &&
    (isActive || (lastVisited !== undefined && aliases.includes(lastVisited)))
  ) {
    return "failed";
  }
  if (isActive && graph.status === "running") return "active";
  if (hasVisited || (graph.status === "done" && selected)) return "done";
  return selected ? "queued" : "skipped";
}

export function BdAskAgenticGraph({ turn }: { turn: AskTurn | null }) {
  const graph = turn?.graph ?? {
    route: "",
    active_node: null,
    visited_nodes: [],
    retrieval_sources: [],
    status: "idle" as const,
  };
  const deep = graph.route === "deep" || turn?.mode === "deep";
  const selectedRoute = graph.route || (turn?.mode ? (deep ? "deep" : "standard") : "");
  const isSelected = (key: string) => key === selectedRoute;
  const retrievalStatus = stepStatus(graph, ["run_subagent"], true);
  const sources = new Set(graph.retrieval_sources.map((source) => source.toLowerCase()));
  const sourceDetail = (source: "qdrant" | "neo4j") => {
    if (retrievalStatus === "queued") return "Waiting for retrieval";
    if (retrievalStatus === "active") return "Searching in parallel";
    return [...sources].some((item) => item.includes(source)) ? "Evidence returned" : "Lookup completed";
  };

  const steps: AgenticGraphStep[] = [
    {
      id: "plan_research",
      label: "Plan book research",
      description: "Choose focused search queries and a retrieval route for the question.",
      status: stepStatus(graph, ["plan_research"], true),
      detail: turn?.deep?.planText || undefined,
    },
    {
      id: "run_subagent",
      label: "Retrieve book evidence",
      description: "Search both indexed evidence stores in parallel.",
      status: retrievalStatus,
      children: [
        {
          id: "qdrant",
          label: "Qdrant vector search",
          detail: sourceDetail("qdrant"),
          status: retrievalStatus,
        },
        { id: "neo4j", label: "Neo4j graph search", detail: sourceDetail("neo4j"), status: retrievalStatus },
      ],
    },
    {
      id: "grade_documents",
      label: "Grade retrieved evidence",
      description: "Check relevance, with a query refinement loop when coverage is weak.",
      status: stepStatus(graph, ["grade_documents"], true),
    },
  ];

  steps.push({
    id: "rewrite_query",
    label: "Refine and search again (when needed)",
    description: "The graph can return to retrieval when evidence needs better coverage.",
    status: stepStatus(
      graph,
      ["rewrite_query"],
      graph.status !== "done" || graph.visited_nodes.includes("rewrite_query"),
    ),
  });

  steps.push(
    {
      id: "answer_route",
      label: "Choose answer strategy",
      description: "Use direct grounded generation or synthesize planned sub-question findings.",
      status: selectedRoute ? (graph.status === "done" ? "done" : "active") : "queued",
      children: [
        {
          id: "generate",
          label: "Standard · grounded answer",
          status: isSelected("standard")
            ? stepStatus(graph, ["generate"], true)
            : selectedRoute
              ? "skipped"
              : "queued",
        },
        {
          id: "synthesize",
          label: "Deep · synthesize findings",
          status: isSelected("deep")
            ? stepStatus(graph, ["synthesize"], true)
            : selectedRoute
              ? "skipped"
              : "queued",
        },
      ],
    },
    {
      id: "critic",
      label: "Verify answer support",
      description: "Assess grounding and confidence against retrieved book evidence.",
      status: stepStatus(graph, ["critic"], true),
    },
    {
      id: "format_citations",
      label: "Format page citations",
      description: "Link answer references back to the indexed book pages.",
      status: stepStatus(graph, ["format_citations"], true),
    },
  );

  const route = !turn
    ? "Awaiting a question"
    : !graph.route && !turn.mode
      ? "Adaptive · planning retrieval"
      : `${isSelected("deep") ? "Deep" : "Standard"} · book-scoped evidence`;
  return (
    <AgenticGraphPanel
      title="Adaptive Agentic RAG"
      description="BD Ask · book-grounded answers"
      route={route}
      status={turn?.error ? "failed" : graph.status}
      steps={steps}
      footer={
        turn ? <p className="m-0 text-xs text-muted-foreground">Current turn: {turn.question}</p> : undefined
      }
    />
  );
}
