"use client";

import { ChartNoAxesColumn } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import { MermaidTopology } from "@/shared/markdown/mermaid-topology";

import { ActionButton } from "../../components/action-button";
import { AgenticNodeList, AgenticPills, AgenticStatusBadge } from "../../components/agentic-parts";
import { ToggleGroup } from "../../components/toggle-group";
import type { AgenticNodeStatus, AgenticNodeView } from "../../domain/agentic-progress";
import type { RagIndexStatus } from "../index-entities";
import { PipelineUsageSheet } from "./pipeline-usage-sheet";

const AgenticFlow = dynamic(
  () => import("../../components/agentic-flow").then((module) => module.AgenticFlow),
  {
    ssr: false,
    loading: () => (
      <p role="status" className="text-xs text-muted-foreground">
        Loading graph…
      </p>
    ),
  },
);

const MODES = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
  { id: "langgraph", label: "LangGraph" },
] as const;

const RAG_LANGGRAPH_TOPOLOGY = `flowchart TD
  start([Start]) --> pin_sources["Pin book sources"]
  pin_sources --> build_vector_index["Build hybrid index"]
  build_vector_index --> materialize_knowledge_graph["Link book knowledge"]
  materialize_knowledge_graph --> publish_retrieval_index["Publish retrieval index"]
  publish_retrieval_index --> finish([End])`;

const NODES = [
  {
    id: "pin_sources",
    label: "Pin book sources",
    detail:
      "Bind the retrieval run to all extracted pages, the published Learning Guide, and the page-selection metadata.",
    reads: ["Ingestion pages", "Page selection", "Learning Guide"],
    writes: ["Pinned source manifest"],
  },
  {
    id: "build_vector_index",
    label: "Build hybrid index",
    detail:
      "Index all extracted pages, learning units, and guide passages for semantic and lexical retrieval, retaining each page’s selection metadata.",
    reads: ["Pinned source manifest", "Normalized book text"],
    writes: ["Passage vectors", "Page-level citations"],
  },
  {
    id: "materialize_knowledge_graph",
    label: "Link book knowledge",
    detail:
      "Connect chapters, lessons, glossary concepts, diagrams, and cited source pages in a book-scoped graph.",
    reads: ["Verified Learning Guide", "Citation references"],
    writes: ["Book-scoped concepts", "Evidence-linked relationships"],
  },
  {
    id: "publish_retrieval_index",
    label: "Publish retrieval index",
    detail: "Record both retrieval stores and source counts for later book-search workflows.",
    reads: ["Hybrid index", "Evidence-linked graph"],
    writes: ["Retrieval manifest", "Index quality counters"],
  },
] as const;

type NodeId = (typeof NODES)[number]["id"];

export function RagIndexBoard({
  index,
  distillComplete,
  executionEnabled,
  capabilityError,
  startPending,
  cancelPending,
  wipePending,
  onStart,
  onCancel,
  onWipe,
}: {
  index: RagIndexStatus | null;
  distillComplete: boolean;
  executionEnabled: boolean;
  capabilityError: string | null;
  startPending: boolean;
  cancelPending: boolean;
  wipePending: boolean;
  onStart: () => void;
  onCancel: () => void;
  onWipe: () => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<NodeId>("pin_sources");
  const [usageOpen, setUsageOpen] = useState(false);
  const views = nodeViews(index);
  const selectedNode = NODES.find((node) => node.id === selectedNodeId);
  const active = index?.status === "queued" || index?.status === "running";
  const resumable = index?.status === "failed" || index?.status === "cancelled";
  const hasRun = index !== null && index.status !== "not_started";

  return (
    <section
      aria-label="Agentic RAG pipeline"
      className="min-w-0 rounded-md border border-border bg-card p-3"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="mono-label">Book Distiller · Retrieval</p>
          <h3 className="mt-1 text-sm font-medium">Agentic RAG</h3>
          <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
            {caption(index, executionEnabled)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ActionButton
            onClick={onStart}
            disabled={
              !executionEnabled ||
              !distillComplete ||
              index?.status === "done" ||
              active ||
              startPending ||
              cancelPending
            }
            aria-busy={startPending}
            aria-label={resumable ? "Resume Agentic RAG" : "Start Agentic RAG"}
            title={
              capabilityError ??
              (!executionEnabled
                ? "Agentic RAG execution is disabled in this environment."
                : index?.status === "done"
                  ? "This retrieval index is complete."
                  : !distillComplete
                    ? "Complete Distill before starting retrieval indexing."
                    : undefined)
            }
          >
            {startPending ? (resumable ? "Resuming…" : "Starting…") : resumable ? "Resume" : "Start"}
          </ActionButton>
          <ActionButton
            onClick={onCancel}
            disabled={!active || index?.cancel_requested === true || cancelPending}
            aria-busy={cancelPending || index?.cancel_requested === true}
            aria-label="Stop Agentic RAG"
            title={active ? undefined : "There is no active retrieval run to stop."}
          >
            {cancelPending || index?.cancel_requested === true ? "Stopping…" : "Stop"}
          </ActionButton>
          <ActionButton
            onClick={onWipe}
            disabled={!hasRun || active || wipePending || startPending || cancelPending}
            aria-busy={wipePending}
            aria-label="Wipe Agentic RAG index"
            title={active ? "Stop the active retrieval run before wiping its index." : undefined}
          >
            {wipePending ? "Wiping…" : "Wipe"}
          </ActionButton>
        </div>
      </header>

      {capabilityError ? (
        <p
          role="status"
          className="mt-3 rounded-sm border border-border bg-background p-2 text-xs text-muted-foreground"
        >
          Execution availability could not be loaded. {capabilityError}
        </p>
      ) : null}
      {index?.error ? (
        <p
          role="alert"
          className="mt-3 rounded-sm border border-destructive bg-background p-2 text-xs text-destructive-text"
        >
          {index.error}
        </p>
      ) : null}

      <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(13rem,1.1fr)_minmax(10rem,0.9fr)]">
        <div className="min-w-0">
          {mode === "graph" ? (
            <AgenticFlow views={views} onOpenNode={selectNode} ariaLabel="Agentic RAG pipeline graph" />
          ) : mode === "list" ? (
            <AgenticNodeList rows={views} onOpen={selectNode} label="Agentic RAG pipeline nodes" />
          ) : (
            <MermaidTopology
              title="Agentic RAG"
              source={RAG_LANGGRAPH_TOPOLOGY}
              note="Configured node flow, in execution order."
            />
          )}
        </div>
        <div className="flex min-w-0 flex-col">
          <div className="mb-3 flex min-w-0 flex-col items-stretch gap-2">
            <ActionButton
              className="w-full justify-center whitespace-nowrap px-2"
              onClick={() => setUsageOpen(true)}
              aria-haspopup="dialog"
            >
              <ChartNoAxesColumn aria-hidden />
              LLM usage
            </ActionButton>
            <div className="flex justify-end">
              <ToggleGroup
                legend="Agentic RAG pipeline shown as"
                options={MODES}
                value={mode}
                onChange={setMode}
              />
            </div>
          </div>
          <section
            aria-label="Retrieval progress"
            className="min-w-0 rounded-sm border border-border bg-background p-3 xl:min-h-72 xl:flex-1"
          >
            {selectedNode ? (
              <>
                <header className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="text-sm font-medium">{selectedNode.label}</h4>
                  <AgenticStatusBadge
                    status={views.find((view) => view.id === selectedNodeId)?.status ?? "pending"}
                  />
                </header>
                <p className="mt-2 text-xs text-muted-foreground">{selectedNode.detail}</p>
                <div className="mt-3 grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
                  <span>Reads</span>
                  <AgenticPills items={selectedNode.reads} />
                  <span>Writes</span>
                  <AgenticPills items={selectedNode.writes} />
                </div>
              </>
            ) : null}
            <div className="mt-4 flex flex-col gap-2 border-t border-border pt-3">
              <Metric label="Source pages" value={index?.source_page_count ?? 0} />
              <Metric label="Indexed passages" value={index?.point_count ?? 0} />
              <Metric label="Knowledge nodes" value={index?.graph_node_count ?? 0} />
              <Metric label="Evidence links" value={index?.graph_relationship_count ?? 0} />
            </div>
          </section>
        </div>
      </div>
      {usageOpen ? (
        <PipelineUsageSheet
          threadId={index && index.status !== "not_started" ? index.distill_run_id : null}
          live={active}
          title="Agentic RAG LLM usage"
          subtitle="Calls and token counts for this retrieval run."
          emptyMessage="No retrieval run yet. Usage appears here after indexing starts."
          onClose={() => setUsageOpen(false)}
        />
      ) : null}
    </section>
  );

  function selectNode(nodeId: string) {
    if (isNodeId(nodeId)) setSelectedNodeId(nodeId);
  }
}

function isNodeId(nodeId: string): nodeId is NodeId {
  return NODES.some((node) => node.id === nodeId);
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums text-foreground">{value.toLocaleString()}</span>
    </div>
  );
}

function nodeViews(index: RagIndexStatus | null): AgenticNodeView[] {
  return NODES.map((node) => {
    const status = stageStatus(index, node.id);
    let kpi: string | null = null;
    let liveLine: string | null = null;
    if (status === "done" && node.id === "build_vector_index" && index) {
      kpi = `${index.point_count.toLocaleString()} passages`;
    } else if (status === "done" && node.id === "materialize_knowledge_graph" && index) {
      kpi = `${index.graph_node_count.toLocaleString()} nodes`;
    }
    if (index?.status === "running" && status === "running") {
      liveLine =
        index.current_stage === "vector_index"
          ? `${index.point_count.toLocaleString()} passages`
          : `${index.graph_node_count.toLocaleString()} nodes · ${index.graph_relationship_count.toLocaleString()} links`;
    }
    return { id: node.id, label: node.label, status, kpi, liveLine };
  });
}

function stageStatus(index: RagIndexStatus | null, nodeId: NodeId): AgenticNodeStatus {
  if (index === null || index.status === "not_started") return "pending";
  if (index.status === "done") return "done";
  if (nodeId === "pin_sources") return "done";
  if (nodeId === "build_vector_index") {
    if (index.vector_status === "done") return "done";
    if (
      index.vector_status === "failed" ||
      (index.status === "failed" && index.current_stage === "vector_index")
    )
      return "failed";
    if (index.vector_status === "running") return "running";
  }
  if (nodeId === "materialize_knowledge_graph") {
    if (index.graph_status === "done") return "done";
    if (
      index.graph_status === "failed" ||
      (index.status === "failed" && index.current_stage === "graph_materialization")
    )
      return "failed";
    if (index.graph_status === "running") return "running";
  }
  if (
    nodeId === "publish_retrieval_index" &&
    index.status === "running" &&
    index.current_stage === "complete"
  )
    return "running";
  return "pending";
}

function caption(index: RagIndexStatus | null, executionEnabled: boolean): string {
  if (!executionEnabled && (index === null || index.status === "not_started"))
    return "Execution is disabled in this environment.";
  if (index === null || index.status === "not_started") return "Ready after the Distill report is published.";
  if (index.status === "queued") return "Retrieval run queued · waiting for a worker.";
  if (index.status === "running") {
    return index.cancel_requested
      ? "Stop requested · waiting for the active batch to finish."
      : "Indexing all extracted book pages and verified learning material.";
  }
  if (index.status === "done") return "Hybrid text index and evidence graph are ready.";
  if (index.status === "cancelled") return "Retrieval indexing stopped. Resume to continue.";
  if (index.current_stage === "vector_index")
    return "Retrieval indexing failed while building the text index.";
  if (index.current_stage === "graph_materialization")
    return "Retrieval indexing failed while linking book knowledge.";
  return "Retrieval indexing failed.";
}
