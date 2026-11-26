"use client";

import { Gauge } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import type { AgenticFlowConnection, AgenticFlowPosition } from "../../components/agentic-flow";
import { AgenticNodeDetailsList, AgenticNodeList, AgenticStatusGlyph } from "../../components/agentic-parts";
import { ToggleGroup } from "../../components/toggle-group";
import type { AgenticNodeStatus, AgenticNodeView } from "../../domain/agentic-progress";
import { type AskTurn, askRouteLabel } from "../ask";
import { ASK_MODES } from "../params";
import { AskSources } from "./ask-sources";
import { RagActionButton as Button } from "./rag-action-button";
import { SideSheet } from "./side-sheet";

const AgenticFlow = dynamic(
  () => import("../../components/agentic-flow").then((module) => module.AgenticFlow),
  {
    ssr: false,
    loading: () => (
      <p role="status" className="p-4 text-xs text-muted-foreground">
        Loading graph…
      </p>
    ),
  },
);

const VIEWS = [
  {
    id: "plan_research",
    label: "Understand the question",
    description: "Choose a conversation reply or book research, then select an answer path.",
  },
  {
    id: "direct_answer",
    label: "Conversation reply",
    description: "Answer conversational questions without searching the book or making book claims.",
  },
  {
    id: "prepare_retrieval",
    label: "Prepare book searches",
    description: "Prepare the planned searches against the selected book's retrieval index.",
  },
  {
    id: "elasticsearch",
    label: "Search full-text index",
    description: "Find passages that match the planned searches in the full-text index.",
  },
  {
    id: "qdrant",
    label: "Search vector index",
    description: "Find semantically related passages in the vector index.",
  },
  {
    id: "neo4j",
    label: "Search knowledge graph",
    description: "Find source-linked concepts and relationships in the knowledge graph.",
  },
  {
    id: "fuse_evidence",
    label: "Combine book evidence",
    description: "Merge the retrieval results while preserving their source references.",
  },
  {
    id: "search_external",
    label: "Search configured source",
    description: "Use the optional Search MCP only when the plan calls for current or outside information.",
  },
  {
    id: "grade_documents",
    label: "Check evidence coverage",
    description: "Assess whether the retrieved evidence supports the question.",
  },
  {
    id: "rewrite_query",
    label: "Refine the search",
    description: "If evidence coverage is weak, refine the query and retrieve once more.",
  },
  {
    id: "generate",
    label: "Write a grounded answer",
    description: "Answer from the selected book and external evidence.",
  },
  {
    id: "synthesize",
    label: "Synthesize research",
    description: "Combine the planned sub-question findings into a book-grounded answer.",
  },
  {
    id: "critic",
    label: "Verify answer support",
    description: "Check that answer claims are supported by the retrieved evidence.",
  },
  {
    id: "format_citations",
    label: "Format citations",
    description: "Attach source references to the answer.",
  },
] as const;

type AskNodeId = (typeof VIEWS)[number]["id"];
type AskNodeStatus = "queued" | "active" | "done" | "failed" | "skipped";
type AskView = (typeof VIEWS)[number] & { status: AskNodeStatus; detail: string | null };
type ViewMode = "graph" | "list";

type AskNodeDetails = {
  reads: readonly string[];
  writes: readonly string[];
  llm: string;
  resources: AgenticNodeView["resources"];
};

const NO_RESOURCES: AskNodeDetails["resources"] = { agents: [], skills: [], tools: [] };
const ASK_PLANNER = { agents: ["BD Ask planner"], skills: ["ask_planner"], tools: [] };
const ASK_GRADER = { agents: ["BD Ask evidence grader"], skills: ["ask_grader"], tools: [] };
const ASK_ANSWERER = { agents: ["BD Ask answerer"], skills: ["ask_answerer"], tools: [] };
const ASK_CRITIC = { agents: ["BD Ask evidence critic"], skills: ["ask_critic"], tools: [] };

const NODE_DETAILS: Record<AskNodeId, AskNodeDetails> = {
  plan_research: {
    reads: ["Question", "Recent thread turns", "Optional suggested sub-questions"],
    writes: ["Intent", "Response effort", "Research plan", "Search queries"],
    llm: "Planner DeepAgent returns a validated response plan.",
    resources: ASK_PLANNER,
  },
  direct_answer: {
    reads: ["Question", "Recent thread turns"],
    writes: ["Conversation reply"],
    llm: "One configured chat-model call with a bounded retry and timeout.",
    resources: NO_RESOURCES,
  },
  prepare_retrieval: {
    reads: ["Book retrieval index manifest", "Planned search queries"],
    writes: ["Prepared book retrieval queries"],
    llm: "Deterministic query preparation; no model call.",
    resources: NO_RESOURCES,
  },
  elasticsearch: {
    reads: ["Prepared book retrieval queries", "Full-text index manifest"],
    writes: ["Ranked full-text passages", "Source status"],
    llm: "ElasticSearch retrieval specialist searches the selected book index.",
    resources: {
      agents: ["ElasticSearch retrieval DeepAgent"],
      skills: [],
      tools: ["bd_search_elasticsearch"],
    },
  },
  qdrant: {
    reads: ["Prepared book retrieval queries", "Vector index manifest"],
    writes: ["Ranked semantic passages", "Source status"],
    llm: "Qdrant retrieval specialist searches the selected book index.",
    resources: {
      agents: ["Qdrant retrieval DeepAgent"],
      skills: [],
      tools: ["bd_search_qdrant"],
    },
  },
  neo4j: {
    reads: ["Prepared book retrieval queries", "Knowledge graph manifest"],
    writes: ["Ranked graph evidence", "Source status"],
    llm: "Neo4j retrieval specialist searches the selected book graph.",
    resources: {
      agents: ["Neo4j retrieval DeepAgent"],
      skills: [],
      tools: ["bd_search_neo4j"],
    },
  },
  fuse_evidence: {
    reads: ["Elasticsearch, Qdrant and Neo4j results"],
    writes: ["Ranked evidence", "Evidence source labels"],
    llm: "Deterministic result fusion; no model call.",
    resources: NO_RESOURCES,
  },
  search_external: {
    reads: ["Planned outside-information query", "Ready Search MCP connection from Settings"],
    writes: ["External evidence", "Source citations", "Search outcome"],
    llm: "No model call inside this step.",
    resources: { agents: [], skills: [], tools: ["Configured search tool", "Optional page-read tool"] },
  },
  grade_documents: {
    reads: ["Question", "Book and external evidence"],
    writes: ["Relevant evidence", "Coverage decision"],
    llm: "Evidence grader DeepAgent returns a validated relevance assessment.",
    resources: ASK_GRADER,
  },
  rewrite_query: {
    reads: ["Question", "Initial queries", "Retrieval coverage feedback"],
    writes: ["One bounded set of revised queries"],
    llm: "The planner DeepAgent rewrites the search after weak coverage.",
    resources: ASK_PLANNER,
  },
  generate: {
    reads: ["Question", "Recent thread turns", "Selected evidence"],
    writes: ["Grounded answer", "Evidence ids"],
    llm: "Answerer DeepAgent returns a validated answer and evidence references.",
    resources: ASK_ANSWERER,
  },
  synthesize: {
    reads: ["Question", "Research plan", "Sub-question findings", "Selected evidence"],
    writes: ["Synthesized answer", "Evidence ids"],
    llm: "Answerer DeepAgent synthesizes the planned findings into a validated answer.",
    resources: ASK_ANSWERER,
  },
  critic: {
    reads: ["Question", "Draft answer", "Referenced evidence"],
    writes: ["Support assessment", "Confidence score", "Fallback when unsupported"],
    llm: "Evidence critic DeepAgent returns a validated support assessment.",
    resources: ASK_CRITIC,
  },
  format_citations: {
    reads: ["Draft answer", "Selected evidence ids", "Evidence citations"],
    writes: ["Final answer with source references", "Citation records"],
    llm: "Deterministic formatting; no model call.",
    resources: NO_RESOURCES,
  },
};

const CONNECTIONS: readonly AgenticFlowConnection[] = [
  {
    source: "plan_research",
    target: "direct_answer",
    sourceHandle: "source-bottom-left",
  },
  {
    source: "plan_research",
    target: "prepare_retrieval",
    sourceHandle: "source-bottom-right",
  },
  {
    source: "prepare_retrieval",
    target: "elasticsearch",
    sourceHandle: "source-bottom-left",
  },
  {
    source: "prepare_retrieval",
    target: "qdrant",
    sourceHandle: "source-bottom",
  },
  {
    source: "prepare_retrieval",
    target: "neo4j",
    sourceHandle: "source-bottom-right",
  },
  { source: "elasticsearch", target: "fuse_evidence" },
  { source: "qdrant", target: "fuse_evidence" },
  { source: "neo4j", target: "fuse_evidence" },
  {
    source: "fuse_evidence",
    target: "search_external",
    sourceHandle: "source-bottom-left",
    targetHandle: "target-top-right",
  },
  { source: "fuse_evidence", target: "grade_documents" },
  {
    source: "search_external",
    target: "grade_documents",
    sourceHandle: "source-right",
    targetHandle: "target-top-left",
  },
  {
    source: "grade_documents",
    target: "rewrite_query",
    sourceHandle: "source-left",
    targetHandle: "target-right",
  },
  { source: "grade_documents", target: "generate" },
  {
    source: "grade_documents",
    target: "synthesize",
    sourceHandle: "source-bottom-left",
  },
  {
    source: "rewrite_query",
    target: "prepare_retrieval",
    sourceHandle: "source-left",
    targetHandle: "target-bottom-left",
    feedback: true,
  },
  { source: "generate", target: "critic" },
  {
    source: "synthesize",
    target: "critic",
    sourceHandle: "source-right",
    targetHandle: "target-top-right",
  },
  { source: "critic", target: "format_citations" },
];

const POSITIONS: Readonly<Record<string, AgenticFlowPosition>> = {
  plan_research: { x: 284, y: 16 },
  direct_answer: { x: 16, y: 182 },
  prepare_retrieval: { x: 284, y: 182 },
  elasticsearch: { x: 16, y: 348 },
  qdrant: { x: 284, y: 348 },
  neo4j: { x: 552, y: 348 },
  fuse_evidence: { x: 284, y: 514 },
  search_external: { x: 16, y: 732 },
  grade_documents: { x: 284, y: 898 },
  rewrite_query: { x: 16, y: 898 },
  generate: { x: 552, y: 1064 },
  synthesize: { x: 16, y: 1064 },
  critic: { x: 552, y: 1230 },
  format_citations: { x: 552, y: 1396 },
};

const RETRIEVAL_GROUPS = [
  { id: "book_retrieval", label: "Book retrieval subgraph", x: 0, y: 158, width: 800, height: 530 },
] as const;

const VIEW_MODES: readonly { id: ViewMode; label: string }[] = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
];

const MODE_HINT: Record<string, string> = {
  "": "Choose a direct reply or book research, then select retrieval effort automatically.",
  fast: "One quick pass over the best book matches.",
  standard: "Search focused facets, check the evidence, then answer.",
  deep: "Research focused sub-questions, then synthesize the findings.",
};

const STATUS_LABEL: Record<AskNodeStatus, string> = {
  queued: "Waiting",
  active: "Running",
  done: "Done",
  failed: "Failed",
  skipped: "Not selected",
};

function flowStatus(status: AskNodeStatus): AgenticNodeStatus {
  if (status === "active") return "running";
  if (status === "done" || status === "failed") return status;
  return "pending";
}

function graphState(turn: AskTurn | null): AskTurn["graph"] {
  const graph = turn?.graph ?? {
    route: "",
    active_node: null,
    visited_nodes: [],
    retrieval_sources: [],
    status: "idle" as const,
  };
  return turn?.error && graph.status !== "failed" ? { ...graph, status: "failed" } : graph;
}

function stepStatus(graph: AskTurn["graph"], aliases: readonly string[], selected: boolean): AskNodeStatus {
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

function hasSource(sources: ReadonlySet<string>, source: "elasticsearch" | "qdrant" | "neo4j"): boolean {
  return [...sources].some(
    (item) => item.includes(source) || (source === "elasticsearch" && item.includes("bm25")),
  );
}

function makeViews(turn: AskTurn | null): AskView[] {
  const graph = graphState(turn);
  const sources = new Set(graph.retrieval_sources.map((source) => source.toLowerCase()));
  const route = graph.route || turn?.mode || "";
  const routeKnown = Boolean(route);
  const isConversation = route === "conversation";
  const bookRoute = !isConversation;
  const externalVisited = graph.visited_nodes.includes("search_external");
  const externalNotRequested = turn?.externalSearchStatus?.toLowerCase() === "not_requested";
  const externalPastDecision =
    graph.visited_nodes.includes("grade_documents") ||
    (isConversation && graph.visited_nodes.includes("direct_answer") && !externalVisited);
  const externalSkipped =
    externalNotRequested ||
    (graph.status === "done" && !externalVisited) ||
    (externalPastDecision && !externalVisited);
  let externalStatus: AskNodeStatus = externalSkipped
    ? "skipped"
    : stepStatus(graph, ["search_external"], true);
  if (turn?.externalSearchStatus === "unavailable" && graph.status !== "failed") {
    externalStatus = "failed";
  }
  const rewriteVisited = graph.visited_nodes.includes("rewrite_query");
  const rewriteResolved =
    rewriteVisited || graph.visited_nodes.includes("generate") || graph.visited_nodes.includes("synthesize");
  const rewriteStatus =
    !bookRoute || ((graph.status === "done" || rewriteResolved) && !rewriteVisited)
      ? "skipped"
      : stepStatus(graph, ["rewrite_query"], true);

  const statusFor = (id: AskNodeId): AskNodeStatus => {
    if (id === "plan_research") return stepStatus(graph, [id], true);
    if (id === "direct_answer")
      return isConversation ? stepStatus(graph, [id], true) : routeKnown ? "skipped" : "queued";
    if (id === "prepare_retrieval") {
      if (graph.active_node === "retrieve_evidence") return "active";
      if (
        graph.status === "failed" &&
        graph.visited_nodes.at(-1) === "retrieve_evidence" &&
        !graph.visited_nodes.some((node) =>
          ["prepare_retrieval", "elasticsearch", "qdrant", "neo4j", "fuse_evidence"].includes(node),
        )
      ) {
        return "failed";
      }
      return stepStatus(graph, [id], bookRoute);
    }
    if (id === "elasticsearch" || id === "qdrant" || id === "neo4j" || id === "fuse_evidence") {
      return stepStatus(graph, [id], bookRoute);
    }
    if (id === "search_external") return externalStatus;
    if (id === "grade_documents") return stepStatus(graph, [id], bookRoute);
    if (id === "rewrite_query") return rewriteStatus;
    if (id === "generate")
      return !isConversation && route !== "deep"
        ? stepStatus(graph, [id], true)
        : routeKnown
          ? "skipped"
          : "queued";
    if (id === "synthesize")
      return route === "deep" ? stepStatus(graph, [id], true) : routeKnown ? "skipped" : "queued";
    if (id === "critic" || id === "format_citations") return stepStatus(graph, [id], bookRoute);
    return "queued";
  };

  return VIEWS.map((view) => {
    const status = statusFor(view.id);
    let detail: string | null = null;
    if (view.id === "plan_research") detail = turn?.deep?.planText || null;
    if (view.id === "prepare_retrieval" && status === "active") detail = "Preparing book searches.";
    if (view.id === "elasticsearch") {
      detail =
        status === "active"
          ? "Searching the full-text index."
          : status === "done"
            ? externalStatusText(status, hasSource(sources, "elasticsearch"))
            : null;
    }
    if (view.id === "qdrant") {
      detail =
        status === "active"
          ? "Searching the vector index."
          : status === "done"
            ? externalStatusText(status, hasSource(sources, "qdrant"))
            : null;
    }
    if (view.id === "neo4j") {
      detail =
        status === "active"
          ? "Searching the knowledge graph."
          : status === "done"
            ? externalStatusText(status, hasSource(sources, "neo4j"))
            : null;
    }
    if (view.id === "fuse_evidence") {
      detail =
        status === "active"
          ? "Combining book evidence."
          : status === "done"
            ? `${graph.retrieval_sources.length} source${graph.retrieval_sources.length === 1 ? "" : "s"} returned evidence.`
            : null;
    }
    if (view.id === "search_external") {
      const external = turn?.externalSearchStatus?.toLowerCase();
      detail =
        external === "complete"
          ? "External sources were added to the evidence set."
          : external === "no_results"
            ? "The configured search returned no usable sources."
            : external === "unavailable"
              ? "The configured search source could not be used; book evidence remains available."
              : external === "invalid_query"
                ? "A search query could not be prepared."
                : status === "skipped"
                  ? "Not used for this question."
                  : "Optional. Used when the question needs current or outside information.";
    }
    if (view.id === "rewrite_query" && status === "done")
      detail = "The search query was refined and retrieval ran again.";
    if (view.id === "critic" && turn?.confidence !== null && turn?.confidence !== undefined) {
      detail = `Confidence assessment: ${(turn.confidence * 100).toFixed(0)}%.`;
    }
    if (view.id === "format_citations" && turn?.citations.length) {
      detail = `${turn.citations.length} source${turn.citations.length === 1 ? "" : "s"} attached to the answer.`;
    }
    if (
      (view.id === "generate" || view.id === "synthesize" || view.id === "direct_answer") &&
      status === "active"
    ) {
      detail = "Writing the response.";
    }
    return { ...view, status, detail };
  });
}

function toNodeViews(steps: readonly AskView[]): AgenticNodeView[] {
  return steps.map((step) => ({
    id: step.id,
    label: step.label,
    status: flowStatus(step.status),
    statusLabel: STATUS_LABEL[step.status],
    kpi: STATUS_LABEL[step.status],
    liveLine: step.detail,
    resources: NODE_DETAILS[step.id].resources,
    progress: null,
  }));
}

function externalStatusText(status: AskNodeStatus, included: boolean): string {
  if (status === "skipped") return "Not used for this question.";
  if (status === "queued") return "Waiting for the retrieval step.";
  if (status === "active") return "Searching the configured external source.";
  if (status === "failed") return "Search was unavailable or could not complete.";
  return included
    ? "Evidence from this index was included."
    : "No evidence tagged from this index was returned.";
}

function AskNodeSheet({ step, turn, onClose }: { step: AskView; turn: AskTurn | null; onClose: () => void }) {
  const details = NODE_DETAILS[step.id];
  return (
    <SideSheet
      title={step.label}
      subtitle={`LangGraph node · ${step.id}`}
      closeLabel={`Close ${step.label} details`}
      onClose={onClose}
    >
      <div className="flex flex-col gap-4">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <AgenticStatusGlyph status={flowStatus(step.status)} />
          {STATUS_LABEL[step.status]}
        </span>
        <section>
          <h3 className="text-sm font-medium">What this step does</h3>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{step.description}</p>
          {step.detail ? <p className="mt-2 break-words text-sm leading-relaxed">{step.detail}</p> : null}
        </section>
        <section className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
          <div>
            <h3 className="text-sm font-medium">Reads</h3>
            <ul className="mt-1 flex list-disc flex-col gap-1 pl-5 text-xs leading-relaxed text-muted-foreground">
              {details.reads.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="text-sm font-medium">Writes</h3>
            <ul className="mt-1 flex list-disc flex-col gap-1 pl-5 text-xs leading-relaxed text-muted-foreground">
              {details.writes.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </section>
        <section>
          <h3 className="text-sm font-medium">
            {step.id === "elasticsearch" || step.id === "qdrant" || step.id === "neo4j"
              ? "Retrieval resources"
              : "Agent resources"}
          </h3>
          <AgenticNodeDetailsList resources={details.resources} />
          <div className="mt-3">
            <h4 className="text-xs font-medium text-muted-foreground">Model work</h4>
            <p className="mt-1 text-xs leading-relaxed">{details.llm}</p>
          </div>
        </section>
        {step.id === "search_external" ? (
          <section className="rounded-sm border border-border bg-muted/30 p-3">
            <h3 className="text-sm font-medium">Optional Search MCP</h3>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              This lane runs only when a ready connection is configured in Settings and the plan requests
              outside information.
            </p>
            <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
              The shared retrieval graph also provides <code className="font-mono">bd_search_web</code> for
              callers that opt into its Search MCP specialist.
            </p>
          </section>
        ) : null}
        {step.id === "plan_research" && turn?.deep ? (
          <section>
            <h3 className="text-sm font-medium">Research plan</h3>
            {turn.deep.planText ? (
              <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">{turn.deep.planText}</p>
            ) : null}
            {turn.deep.cards.length ? (
              <ol className="mt-3 flex list-decimal flex-col gap-3 pl-5">
                {turn.deep.cards.map((card) => (
                  <li key={card.question} className="pl-1 text-sm">
                    <p>{card.question}</p>
                    {card.answer ? (
                      <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                        {card.answer}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : null}
          </section>
        ) : null}
        {step.id === "format_citations" ? (
          <section>
            <h3 className="mb-2 text-sm font-medium">Sources attached to this answer</h3>
            <AskSources citations={turn?.citations ?? []} highlight={null} heading="Answer sources" />
          </section>
        ) : null}
        {turn?.error ? (
          <p role="alert" className="rounded-sm border border-destructive p-3 text-sm text-destructive-text">
            {turn.error}
          </p>
        ) : null}
      </div>
    </SideSheet>
  );
}

export function BdAskAgenticGraph({
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
  const [selectedNode, setSelectedNode] = useState<AskNodeId | null>(null);
  const steps = makeViews(turn);
  const graph = graphState(turn);
  const route = graph.route || turn?.mode || "";
  const routeLabel = !turn
    ? "Awaiting a question"
    : !route
      ? "Adaptive · planning"
      : `${askRouteLabel(route, turn.mode)} · ${route === "conversation" ? "direct response" : "book evidence"}`;
  const flowNodes = toNodeViews(steps);
  const activeStep = steps.find((step) => step.id === selectedNode);
  const overallStatus = turn?.error ? "failed" : graph.status;
  const overallStatusLabel =
    overallStatus === "idle"
      ? "Ready"
      : overallStatus === "done"
        ? "Complete"
        : overallStatus === "failed"
          ? "Failed"
          : "Running";

  return (
    <section
      aria-label="Adaptive Agentic RAG graph"
      className="rounded-sm border border-border bg-background"
    >
      <header className="border-b border-border px-3 py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="m-0 text-sm font-semibold tracking-tight">Adaptive Agentic RAG</h2>
            <p className="mt-1 mb-0 text-xs leading-4 text-muted-foreground">
              BD Ask · live workflow and node details
            </p>
          </div>
          <span
            className={`shrink-0 rounded-sm border px-2 py-1 text-xs ${overallStatus === "failed" ? "border-destructive text-destructive-text" : "border-border text-muted-foreground"}`}
          >
            {overallStatusLabel}
          </span>
        </div>
        <p className="mt-3 mb-0 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Route:</span> {routeLabel}
        </p>
        <div className="mt-2 grid w-full min-w-0 grid-cols-3 gap-2">
          <label htmlFor="bd-ask-mode" className="flex h-10 min-w-0 items-center gap-1 text-xs">
            <span className="shrink-0 font-medium max-[400px]:hidden">Effort</span>
            <select
              id="bd-ask-mode"
              name="bd-ask-mode"
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
          <Button
            type="button"
            onClick={onUsage}
            className="w-full min-w-0 justify-center gap-1 px-1.5 text-xs whitespace-nowrap"
          >
            <Gauge aria-hidden data-icon="inline-start" className="max-[400px]:hidden" />
            LLM usage
          </Button>
          <div className="min-w-0">
            <ToggleGroup
              legend="Adaptive RAG shown as"
              options={VIEW_MODES}
              value={viewMode}
              onChange={setViewMode}
              fullWidth
              compact
            />
          </div>
        </div>
      </header>
      <div className="min-w-0 overflow-hidden">
        {viewMode === "graph" ? (
          <AgenticFlow
            views={flowNodes}
            onOpenNode={(nodeId) => setSelectedNode(nodeId as AskNodeId)}
            ariaLabel="BD Ask LangGraph execution graph"
            connections={CONNECTIONS}
            positions={POSITIONS}
            groups={RETRIEVAL_GROUPS}
            fitView
            fitViewOptions={{ padding: 0.12 }}
            className="h-[min(78vh,900px)] min-h-[500px]"
          />
        ) : (
          <div className="p-3">
            <AgenticNodeList
              rows={flowNodes}
              onOpen={(nodeId) => setSelectedNode(nodeId as AskNodeId)}
              label="BD Ask workflow nodes"
            />
          </div>
        )}
      </div>
      {turn ? (
        <footer className="border-t border-border px-3 py-3">
          <p className="m-0 break-words text-xs text-muted-foreground">Current turn: {turn.question}</p>
        </footer>
      ) : null}
      {activeStep ? (
        <AskNodeSheet step={activeStep} turn={turn} onClose={() => setSelectedNode(null)} />
      ) : null}
    </section>
  );
}
