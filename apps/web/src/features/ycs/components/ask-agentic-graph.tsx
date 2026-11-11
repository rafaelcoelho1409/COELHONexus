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

function routeName(turn: AskTurn | null): string {
  if (!turn) return "Awaiting a question";
  const route = turn.graph.route;
  if (route === "conversation") return "Conversation · direct answer";
  if (route === "deep" || turn.mode === "deep") return "Deep · multi-agent research";
  if (route === "standard" || turn.mode)
    return `${turn.mode === "fast" ? "Fast" : "Standard"} · video evidence`;
  return "Adaptive · choosing a route";
}

export function YcsAskAgenticGraph({ turn }: { turn: AskTurn | null }) {
  const graph = turn?.graph ?? {
    route: "",
    active_node: null,
    visited_nodes: [],
    retrieval_sources: [],
    status: "idle" as const,
  };
  const route = graph.route || (turn?.mode === "deep" ? "deep" : turn?.mode ? "standard" : "");
  const conversation = route === "conversation";
  const deep = route === "deep" || turn?.mode === "deep";
  const standard = route === "standard";
  const selected = (key: string) => route === key;
  const routed = graph.visited_nodes.includes("prepare") || Boolean(route);
  const branchStatus = (key: string, aliases: string[]): AgenticStepStatus => {
    if (!route) return "queued";
    return selected(key) ? stepStatus(graph, aliases, true) : "skipped";
  };

  const steps: AgenticGraphStep[] = [
    {
      id: "prepare",
      label: "Understand question and choose route",
      description: "Resolve context, classify intent, and select the amount of retrieval needed.",
      status: stepStatus(graph, ["prepare", "classify_query", "contextualize"], true),
      detail: turn?.stages.retrieve.action || undefined,
    },
    {
      id: "route",
      label: "Adaptive route",
      description: "Only the selected route runs for this turn.",
      status: routed ? (graph.status === "running" ? "active" : "done") : "queued",
      children: [
        {
          id: "conversation",
          label: "Conversation",
          status: branchStatus("conversation", ["direct_answer"]),
        },
        {
          id: "standard",
          label: "Video retrieval",
          status: branchStatus("standard", ["run_standard", "retrieve"]),
        },
        { id: "deep", label: "Deep research", status: branchStatus("deep", ["plan_research"]) },
      ],
    },
  ];

  if (conversation) {
    steps.push({
      id: "direct_answer",
      label: "Compose conversational answer",
      description: "Answer greetings and context-only turns without making transcript claims.",
      status: stepStatus(graph, ["direct_answer"], true),
    });
  } else if (deep) {
    steps.push(
      {
        id: "plan_research",
        label: "Plan focused research",
        description: "Break the question into evidence-seeking sub-questions.",
        status: stepStatus(graph, ["plan_research"], true),
        detail: turn?.deep?.planText || undefined,
      },
      {
        id: "run_subagents",
        label: "Research in parallel",
        description: "Each research sub-agent searches the selected video channels.",
        status: stepStatus(graph, ["run_subagents", "run_subagent"], true),
        children: (turn?.deep?.cards ?? []).map((card, index) => ({
          id: `sub-question-${index}`,
          label: card.question,
          status:
            card.status === "done" ? "done" : stepStatus(graph, ["run_subagents", "run_subagent"], true),
          detail: card.answer ? card.answer.slice(0, 120) : undefined,
        })),
      },
      {
        id: "synthesize",
        label: "Synthesize findings",
        description: "Merge the sub-agent evidence into one grounded answer.",
        status: stepStatus(graph, ["synthesize"], true),
      },
      {
        id: "critic",
        label: "Check answer support",
        description: "Assess grounding and confidence before returning the answer.",
        status: stepStatus(graph, ["critic"], true),
      },
    );
  } else if (standard) {
    steps.push({
      id: "run_standard",
      label: "Run video evidence pipeline",
      description: "Retrieve, grade, and verify transcript evidence; refine the query when needed.",
      status: stepStatus(
        graph,
        [
          "run_standard",
          "retrieve",
          "rewrite_query",
          "grade_documents",
          "generate",
          "check_hallucination",
          "format_citations",
        ],
        true,
      ),
      children: [
        { id: "retrieve", label: "Retrieve transcripts", status: stepStatus(graph, ["retrieve"], true) },
        {
          id: "grade_documents",
          label: "Grade evidence",
          status: stepStatus(graph, ["grade_documents"], true),
        },
        {
          id: "rewrite_query",
          label: "Refine search if needed",
          status: stepStatus(
            graph,
            ["rewrite_query"],
            graph.status !== "done" || graph.visited_nodes.includes("rewrite_query"),
          ),
        },
        { id: "generate", label: "Generate grounded answer", status: stepStatus(graph, ["generate"], true) },
        {
          id: "verify",
          label: "Verify and format citations",
          status: stepStatus(graph, ["check_hallucination", "format_citations"], true),
        },
      ],
    });
  } else {
    steps.push({
      id: "route_pending",
      label: "Waiting for the adaptive route",
      description: "YCS will choose a direct conversation, video retrieval, or deep research path.",
      status: graph.status === "running" ? "active" : "queued",
    });
  }

  const activeStatus = turn?.error ? "failed" : graph.status;
  return (
    <AgenticGraphPanel
      title="Adaptive Agentic RAG"
      description="YCS Ask · transcript-grounded answers"
      route={routeName(turn)}
      status={activeStatus}
      steps={steps}
      footer={
        turn ? <p className="m-0 text-xs text-muted-foreground">Current turn: {turn.question}</p> : undefined
      }
    />
  );
}
