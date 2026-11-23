"use client";

/** Adaptive YCS Ask LangGraph with live node state, graph/list views, and per-node resources. */

import { Gauge } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import type { AskTurn } from "../ask";
import { ASK_MODES } from "../params";
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

const MODE_HINT: Record<string, string> = {
  "": "Picks Fast, Standard or Deep for each question.",
  fast: "One quick pass over the best video matches.",
  standard: "Retrieve, grade, then answer from transcript evidence.",
  deep: "Plans sub-questions, researches each, then merges the findings.",
};

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
  retrieve: {
    agents: [],
    skills: ["Search indexed transcripts in the selected channel scope"],
    tools: ["Elasticsearch", "Qdrant", "Neo4j"],
  },
  grade_documents: {
    agents: [],
    skills: ["Keep transcript passages relevant to the question"],
    tools: ["Document grader", "Chat model"],
  },
  rewrite_query: {
    agents: [],
    skills: ["Refine the search when evidence is insufficient"],
    tools: ["Chat model"],
  },
  generate: {
    agents: [],
    skills: ["Write an answer grounded in transcript evidence and conversation history"],
    tools: ["Chat model"],
  },
  check_hallucination: {
    agents: [],
    skills: ["Check answer claims against retrieved transcripts"],
    tools: ["Chat model"],
  },
  fallback_answer: {
    agents: [],
    skills: ["Provide a candid answer when no transcript evidence passes grading"],
    tools: ["Chat model", "Optional external search"],
  },
  corroborate: {
    agents: [],
    skills: ["Check an unverified answer against external sources"],
    tools: ["Chat model", "Optional external search"],
  },
  format_citations: {
    agents: [],
    skills: ["Attach transcript source cards to the answer"],
    tools: [],
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
    id: "retrieve",
    label: "Retrieve transcript evidence",
    description:
      "Search Elasticsearch, Qdrant, and Neo4j in the selected channel scope, merging the best transcript matches.",
    group: "standard",
    aliases: ["retrieve", "run_standard"],
  },
  {
    id: "grade_documents",
    label: "Grade transcript evidence",
    description:
      "Assess which retrieved transcript passages directly support the question before answer generation.",
    group: "standard",
    aliases: ["grade_documents"],
  },
  {
    id: "rewrite_query",
    label: "Refine the retrieval query",
    description:
      "Rewrite the search when grading finds insufficient evidence, then run one bounded retrieval retry.",
    group: "standard",
    aliases: ["rewrite_query"],
  },
  {
    id: "generate",
    label: "Generate a grounded answer",
    description: "Write a response from the relevant transcript passages and recent conversation history.",
    group: "standard",
    aliases: ["generate"],
  },
  {
    id: "check_hallucination",
    label: "Verify transcript support",
    description: "Check whether the generated claims are grounded in the retrieved transcript passages.",
    group: "standard",
    aliases: ["check_hallucination"],
  },
  {
    id: "fallback_answer",
    label: "Answer with limited evidence",
    description:
      "When no transcript passage passes grading, use the closest matches and available external context while stating the evidence limits.",
    group: "standard",
    aliases: ["fallback_answer"],
  },
  {
    id: "corroborate",
    label: "Corroborate an uncertain answer",
    description:
      "After the bounded rewrite path is exhausted, make a best-effort external check and add a short note when the evidence is decisive.",
    group: "standard",
    aliases: ["corroborate"],
  },
  {
    id: "format_citations",
    label: "Format video citations",
    description:
      "Build the deduplicated source cards that connect the answer to its videos and transcript passages.",
    group: "standard",
    aliases: ["format_citations"],
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
  if (graph.status === "done" && group !== "shared") return "skipped";
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
  if ((nodeId === "retrieve" || nodeId === "run_subagents") && turn.graph.retrieval_sources.length > 0) {
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
  if (nodeId === "direct_answer" || nodeId === "generate" || nodeId === "fallback_answer") {
    return turn.stages.generate.action || undefined;
  }
  if (nodeId === "grade_documents") return turn.stages.grade.action || undefined;
  if (nodeId === "rewrite_query") return turn.stages.retrieve.action || undefined;
  if (nodeId === "check_hallucination" || nodeId === "corroborate" || nodeId === "critic") {
    if (turn.confidence !== null) return `Confidence ${(turn.confidence * 100).toFixed(0)}%`;
    return turn.stages.verify.action || undefined;
  }
  if (nodeId === "format_citations" && turn.citations.length) {
    return `${turn.citations.length} video source${turn.citations.length === 1 ? "" : "s"} attached`;
  }
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

export function YcsAskAgenticGraph({
  turn,
  mode,
  onMode,
  onUsage,
}: {
  turn: AskTurn | null;
  mode: string;
  onMode: (mode: string) => void;
  onUsage: () => void;
}) {
  const [viewMode, setViewMode] = useState<ViewMode>("graph");
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

        <div className="mt-2 grid w-full min-w-0 grid-cols-3 gap-2">
          <label htmlFor="ycs-ask-mode" className="flex h-10 min-w-0 items-center gap-1 text-xs">
            <span className="shrink-0 font-medium max-[400px]:hidden">Effort</span>
            <select
              id="ycs-ask-mode"
              name="ycs-ask-mode"
              aria-label="Retrieval effort"
              value={mode}
              title={MODE_HINT[mode] ?? MODE_HINT[""]}
              onChange={(event) => onMode(event.target.value)}
              className="h-10 min-w-0 flex-1 rounded-sm border border-input bg-background px-1.5 text-xs text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {ASK_MODES.map((option) => (
                <option key={option.id || "auto"} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <ActionButton
            type="button"
            onClick={onUsage}
            className="h-10 w-full min-w-0 justify-center gap-1 px-1.5 text-xs whitespace-nowrap"
          >
            <Gauge aria-hidden data-icon="inline-start" className="max-[400px]:hidden" />
            LLM usage
          </ActionButton>
          <fieldset className="m-0 flex min-w-0 overflow-hidden rounded-sm border border-border bg-background p-0">
            <legend className="sr-only">Adaptive RAG shown as</legend>
            {VIEWS.map((view) => (
              <ActionButton
                key={view.id}
                type="button"
                aria-pressed={viewMode === view.id}
                onClick={() => setViewMode(view.id)}
                className="h-10 min-w-0 flex-1 justify-center whitespace-nowrap rounded-none border-0 border-r border-r-border px-1.5 text-xs last:border-r-0"
              >
                {view.label}
              </ActionButton>
            ))}
          </fieldset>
        </div>

        <div className="mt-3 min-w-0 overflow-hidden rounded-sm border border-border bg-background p-1">
          {viewMode === "graph" ? (
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
