"use client";

/** Adaptive YCS Ask LangGraph with live node state, graph/list views, and per-node resources. */

import dynamic from "next/dynamic";
import { useState } from "react";

import type { AskTurn } from "../ask";
import { ActionButton } from "./action-button";
import { AskAgenticNodeList } from "./ask-agentic-node-list";
import { AskAgenticNodeSheet } from "./ask-agentic-node-sheet";
import type { AskAgenticNode, AskAgenticNodeGroup, AskAgenticNodeStatus } from "./ask-agentic-types";
import { StatusBadge } from "./status-badge";

const AskAgenticFlow = dynamic(() => import("./ask-agentic-flow").then((module) => module.AskAgenticFlow), {
  ssr: false,
  loading: () => (
    <p role="status" className="flex min-h-36 items-center justify-center text-xs text-muted-foreground">
      Loading graph…
    </p>
  ),
});

const VIEWS = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
] as const;

type ViewMode = (typeof VIEWS)[number]["id"];
type AskRoute = "conversation" | "standard" | "deep" | "";

const RESOURCES = {
  prepare: {
    agents: [],
    skills: ["Resolve follow-up context", "Classify question intent", "Select an answer route"],
    tools: ["Chat model", "Neo4j channel lookup"],
  },
  direct_answer: {
    agents: [],
    skills: ["Answer conversational turns without transcript claims"],
    tools: ["Chat model"],
  },
  run_standard: {
    agents: [],
    skills: ["Retrieve and grade transcript evidence", "Generate and verify a cited answer"],
    tools: ["Elasticsearch", "Qdrant", "Neo4j"],
  },
  plan_research: {
    agents: ["research_planner"],
    skills: ["Plan focused evidence-seeking sub-questions"],
    tools: ["Chat model"],
  },
  run_subagents: {
    agents: ["YCS Ask research sub-agents"],
    skills: ["Research planned sub-questions", "Collect evidence for synthesis"],
    tools: ["Elasticsearch", "Qdrant", "Neo4j"],
  },
  synthesize: {
    agents: [],
    skills: ["Combine sub-question findings into one answer"],
    tools: ["Chat model"],
  },
  critic: {
    agents: ["research_critic"],
    skills: ["Assess answer support and confidence"],
    tools: ["Chat model"],
  },
} satisfies Record<string, AskAgenticNode["resources"]>;

const NODE_DEFINITIONS: ReadonlyArray<{
  id: keyof typeof RESOURCES;
  label: string;
  description: string;
  group: AskAgenticNodeGroup;
  aliases: readonly string[];
}> = [
  {
    id: "prepare",
    label: "Prepare and route question",
    description:
      "Resolve references to earlier turns and classify intent before selecting conversation, standard retrieval, or deep research.",
    group: "shared",
    aliases: ["prepare", "contextualize", "classify_query"],
  },
  {
    id: "direct_answer",
    label: "Answer conversational turn",
    description:
      "Handle greetings and context-only turns without making claims about video transcripts. If answer generation fails, the graph falls back to standard retrieval.",
    group: "conversation",
    aliases: ["direct_answer"],
  },
  {
    id: "run_standard",
    label: "Run standard video RAG",
    description:
      "Invoke the standard transcript workflow, which retrieves and grades evidence, generates and checks the answer, and formats citations before ending the turn.",
    group: "standard",
    aliases: ["run_standard"],
  },
  {
    id: "plan_research",
    label: "Plan focused research",
    description: "Break a complex question into focused sub-questions for evidence collection.",
    group: "deep",
    aliases: ["plan_research"],
  },
  {
    id: "run_subagents",
    label: "Research sub-questions",
    description:
      "Run bounded research sub-agents for the planned questions and return their evidence for synthesis.",
    group: "deep",
    aliases: ["run_subagents", "run_subagent"],
  },
  {
    id: "synthesize",
    label: "Synthesize research findings",
    description: "Combine the sub-question answers into one coherent response with citations.",
    group: "deep",
    aliases: ["synthesize"],
  },
  {
    id: "critic",
    label: "Review answer support",
    description:
      "Assess whether the synthesized answer is supported by the retrieved evidence, then end the deep research path.",
    group: "deep",
    aliases: ["critic"],
  },
];

const RETRIEVAL_SOURCE_LABELS: Readonly<Record<string, string>> = {
  qdrant_hybrid: "Qdrant hybrid search",
  neo4j_graph: "Neo4j graph search",
  elasticsearch: "Elasticsearch full-text search",
};

function selectedRoute(turn: AskTurn | null): AskRoute {
  if (!turn) return "";
  const route = turn.graph.route.toLowerCase();
  if (["conversation", "direct", "direct_answer"].includes(route) || turn.mode === "conversation") {
    return "conversation";
  }
  if (route === "deep" || turn.mode === "deep") return "deep";
  if (["standard", "fast", "video"].includes(route) || ["standard", "fast"].includes(turn.mode)) {
    return "standard";
  }
  return "";
}

function routeLabel(turn: AskTurn | null, route: AskRoute): string {
  if (!turn) return "Awaiting a question";
  if (route === "conversation") return "Conversation · direct answer";
  if (route === "deep") return "Deep · multi-agent research";
  if (route === "standard") return `${turn.mode === "fast" ? "Fast" : "Standard"} · video evidence`;
  if (turn.graph.status === "running" && turn.graph.active_node === "prepare") {
    return "Adaptive · preparing question and route";
  }
  if (turn.graph.status === "running") return "Adaptive · selecting a route";
  return "Adaptive · route not selected";
}

function nodeStatus(
  turn: AskTurn | null,
  aliases: readonly string[],
  group: AskAgenticNodeGroup,
  route: AskRoute,
): AskAgenticNodeStatus {
  const graph = turn?.graph ?? {
    route: "",
    active_node: null,
    visited_nodes: [],
    retrieval_sources: [],
    status: "idle" as const,
  };
  const active = aliases.includes(graph.active_node ?? "");
  const visited = aliases.some((id) => graph.visited_nodes.includes(id));
  const lastVisited = graph.visited_nodes.at(-1);

  if (graph.status === "failed" && (active || (lastVisited !== undefined && aliases.includes(lastVisited)))) {
    return "failed";
  }
  if (active && graph.status === "running") return "active";
  if (visited) return "done";
  if (group !== "shared" && route && group !== route) return "skipped";
  if (graph.status === "done" && group === "shared") return "done";
  return "queued";
}

function graphStatus(turn: AskTurn | null): {
  tone: "queued" | "running" | "done" | "failed" | "na";
  label: string;
} {
  if (turn?.error || turn?.graph.status === "failed") return { tone: "failed", label: "Failed" };
  if (turn?.streaming || turn?.graph.status === "running") return { tone: "running", label: "Running" };
  if (turn?.graph.status === "done") return { tone: "done", label: "Complete" };
  return { tone: "na", label: "Ready" };
}

function activityForNode(nodeId: keyof typeof RESOURCES, turn: AskTurn | null): string | undefined {
  if (!turn) return undefined;
  if (nodeId === "run_standard" && turn.graph.retrieval_sources.length > 0) {
    return turn.graph.retrieval_sources
      .map((source) => RETRIEVAL_SOURCE_LABELS[source] ?? source)
      .join(" · ");
  }
  if (nodeId === "prepare") return turn.stages.retrieve.action || undefined;
  if (nodeId === "plan_research" && turn.deep?.planText) return turn.deep.planText;
  if (nodeId === "run_subagents" && turn.deep?.cards.length) {
    const completed = turn.deep.cards.filter((card) => card.status === "done").length;
    return `${completed}/${turn.deep.cards.length} research sub-question${turn.deep.cards.length === 1 ? "" : "s"} complete`;
  }
  if (nodeId === "synthesize" && turn.deep?.banner) return turn.deep.banner;
  if (nodeId === "critic" && turn.confidence !== null) {
    return `Confidence ${(turn.confidence * 100).toFixed(0)}%`;
  }
  if (nodeId === "direct_answer") return turn.stages.generate.action || undefined;
  if (nodeId === "run_standard") return turn.stages.generate.action || undefined;
  return undefined;
}

function nodeViews(turn: AskTurn | null): AskAgenticNode[] {
  const route = selectedRoute(turn);
  return NODE_DEFINITIONS.map((definition) => ({
    id: definition.id,
    label: definition.label,
    description: definition.description,
    group: definition.group,
    status: nodeStatus(turn, definition.aliases, definition.group, route),
    resources: RESOURCES[definition.id],
    activity: activityForNode(definition.id, turn),
  }));
}

export function YcsAskAgenticGraph({ turn }: { turn: AskTurn | null }) {
  const [mode, setMode] = useState<ViewMode>("graph");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const nodes = nodeViews(turn);
  const selectedNode = nodes.find((node) => node.id === selectedId);
  const status = graphStatus(turn);
  const route = selectedRoute(turn);

  return (
    <>
      <section
        aria-label="Adaptive Agentic RAG graph"
        className="min-w-0 rounded-md border border-border bg-card p-3"
      >
        <header className="flex min-w-0 items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="m-0 text-sm font-semibold tracking-tight">Adaptive Agentic RAG</h3>
            <p className="mt-1 mb-0 text-xs leading-4 text-muted-foreground">
              YCS Ask · transcript-grounded answers
            </p>
          </div>
          <StatusBadge tone={status.tone} label={status.label} className="shrink-0" />
        </header>

        <p aria-live="polite" className="mt-3 mb-0 break-words text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Route:</span> {routeLabel(turn, route)}
        </p>

        <fieldset className="mt-3 mb-0 flex w-full overflow-hidden rounded-sm border border-border bg-background p-0">
          <legend className="sr-only">Adaptive Agentic RAG shown as</legend>
          {VIEWS.map((view) => (
            <ActionButton
              key={view.id}
              type="button"
              aria-pressed={mode === view.id}
              onClick={() => setMode(view.id)}
              className="min-h-9 min-w-0 flex-1 justify-center whitespace-nowrap rounded-none border-0 border-r border-r-border px-2 last:border-r-0"
            >
              {view.label}
            </ActionButton>
          ))}
        </fieldset>

        <div className="mt-3 min-w-0 overflow-auto rounded-sm border border-border bg-background p-1">
          {mode === "graph" ? (
            <AskAgenticFlow nodes={nodes} onOpen={setSelectedId} />
          ) : (
            <AskAgenticNodeList nodes={nodes} onOpen={setSelectedId} />
          )}
        </div>

        <footer className="mt-3 border-t border-border pt-3">
          {turn ? (
            <p className="m-0 break-words text-xs text-muted-foreground">
              Current question: <span className="text-foreground">{turn.question}</span>
            </p>
          ) : (
            <p className="m-0 text-xs text-muted-foreground">
              Ask a question to see its route and live node status.
            </p>
          )}
        </footer>
      </section>

      {selectedNode ? (
        <AskAgenticNodeSheet node={selectedNode} turn={turn} onClose={() => setSelectedId(null)} />
      ) : null}
    </>
  );
}
