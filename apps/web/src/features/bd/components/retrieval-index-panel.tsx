"use client";

import { ChartNoAxesColumn, Info } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { apiErrorMessage } from "@/shared/lib/api-error";
import type { AgenticNodeResources, AgenticNodeStatus, AgenticNodeView } from "../domain/agentic-progress";
import {
  retrievalIndexNeedsUpdate,
  retrievalIndexStatusLabel,
} from "../domain/retrieval-index";
import type { AgenticRunStatus, RetrievalIndexStatus } from "../entities";
import { SideSheet } from "../rag/components/side-sheet";
import { ActionButton } from "./action-button";
import type { AgenticFlowConnection, AgenticFlowPosition } from "./agentic-flow";
import { AgenticNodeDetailsList, AgenticNodeList, AgenticPills, AgenticStatusBadge } from "./agentic-parts";
import { AgenticUsageSheet } from "./agentic-pipeline";
import { ToggleGroup } from "./toggle-group";
import { useReplayRetrievalIndexNode } from "../hooks/use-bd-data";

const AgenticFlow = dynamic(() => import("./agentic-flow").then((module) => module.AgenticFlow), {
  ssr: false,
  loading: () => (
    <p role="status" className="p-3 text-xs text-muted-foreground">
      Loading graph…
    </p>
  ),
});

const MODES = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
] as const;

const EMPTY_RESOURCES: AgenticNodeResources = { agents: [], skills: [], tools: [] };

const INDEX_NODES = [
  {
    id: "pin_sources",
    label: "Pin processed pages",
    detail:
      "Pins the published processed-book artifact and its page-selection metadata as the immutable source for indexing.",
    reads: ["Processed-book artifact", "Page selection", "Book and run metadata"],
    writes: ["Pinned source manifest"],
    resources: EMPTY_RESOURCES,
  },
  {
    id: "elasticsearch",
    label: "Elasticsearch",
    detail:
      "Builds the book-scoped full-text index from the pinned processed pages, preserving page and section citations.",
    reads: ["Pinned processed pages"],
    writes: ["Elasticsearch documents", "Full-text index metadata"],
    resources: { ...EMPTY_RESOURCES, tools: ["Elasticsearch"] },
  },
  {
    id: "qdrant",
    label: "Qdrant",
    detail:
      "Builds the book-scoped vector index from the pinned processed pages for semantic and hybrid retrieval.",
    reads: ["Pinned processed pages"],
    writes: ["Qdrant points", "Embedding metadata"],
    resources: { ...EMPTY_RESOURCES, tools: ["Qdrant", "Embedding provider"] },
  },
  {
    id: "neo4j",
    label: "Neo4j",
    detail:
      "Extracts book concepts and relationships in batches, then writes the book-scoped knowledge graph.",
    reads: ["Pinned processed pages", "Section context"],
    writes: ["Knowledge-graph nodes", "Evidence-linked relationships"],
    resources: {
      agents: [],
      skills: [],
      tools: ["LangChain LLMGraphTransformer", "Neo4j"],
    },
  },
  {
    id: "publish_retrieval_index",
    label: "Publish retrieval index",
    detail:
      "Validates and publishes the retrieval manifest after the full-text, vector, and graph indexes are ready.",
    reads: ["Elasticsearch index", "Qdrant collection", "Neo4j graph"],
    writes: ["Retrieval-index manifest", "Index counts and status"],
    resources: EMPTY_RESOURCES,
  },
] as const;

type RetrievalNodeId = (typeof INDEX_NODES)[number]["id"];
type PageProgressField =
  | "pin_sources_pages_completed"
  | "elasticsearch_pages_completed"
  | "qdrant_pages_completed"
  | "neo4j_pages_completed";

const PAGE_PROGRESS: Partial<Record<RetrievalNodeId, { field: PageProgressField; label: string }>> = {
  pin_sources: { field: "pin_sources_pages_completed", label: "pages verified" },
  elasticsearch: { field: "elasticsearch_pages_completed", label: "pages indexed" },
  qdrant: { field: "qdrant_pages_completed", label: "pages indexed" },
  neo4j: { field: "neo4j_pages_completed", label: "pages analyzed" },
};

/** Mirrors the backend LangGraph: Elasticsearch fans out to both stores, which feed publication. */
const INDEX_CONNECTIONS: readonly AgenticFlowConnection[] = [
  { source: "pin_sources", target: "elasticsearch" },
  { source: "elasticsearch", target: "qdrant" },
  { source: "elasticsearch", target: "neo4j" },
  { source: "qdrant", target: "publish_retrieval_index" },
  { source: "neo4j", target: "publish_retrieval_index" },
];

const INDEX_POSITIONS: Readonly<Record<RetrievalNodeId, AgenticFlowPosition>> = {
  pin_sources: { x: 258, y: 16 },
  elasticsearch: { x: 258, y: 176 },
  qdrant: { x: 16, y: 336 },
  neo4j: { x: 500, y: 336 },
  publish_retrieval_index: { x: 258, y: 496 },
};

type Props = {
  bookId: string;
  run: AgenticRunStatus | null;
  index: RetrievalIndexStatus | null;
};

export function RetrievalIndexPanel({ bookId, run, index }: Props) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [usageOpen, setUsageOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [indexInfoOpen, setIndexInfoOpen] = useState(false);
  const [replayNodeId, setReplayNodeId] = useState<RetrievalNodeId | null>(null);
  const [replayMessage, setReplayMessage] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<RetrievalNodeId>("pin_sources");
  const replayNode = useReplayRetrievalIndexNode();
  const live = index?.status === "queued" || index?.status === "running";
  const indexOutdated = retrievalIndexNeedsUpdate(index, run);
  const views = nodeViews(index);
  const selectedNode = INDEX_NODES.find((node) => node.id === selectedNodeId);
  const selectedView = views.find((view) => view.id === selectedNodeId);
  const completedCount = views.filter((node) => node.status === "done").length;
  const statusText = retrievalIndexStatusLabel(index, run);
  const canReplay = Boolean(
    index &&
      !live &&
      !indexOutdated &&
      ["done", "failed", "cancelled"].includes(index.status),
  );

  return (
    <section
      aria-labelledby="retrieval-index-graph-heading"
      className="min-w-0 rounded-md border border-border bg-card p-3"
    >
      <header className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id="retrieval-index-graph-heading" className="text-sm font-medium">
            Agentic RAG
          </h3>
        </div>
        <ActionButton
          iconOnly
          onClick={() => setIndexInfoOpen(true)}
          aria-label="Open Agentic RAG details"
          title="Agentic RAG details"
          aria-haspopup="dialog"
        >
          <Info aria-hidden />
        </ActionButton>
      </header>

      <div className="mt-3 grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(8rem,1fr)_minmax(0,2fr)]">
        <ActionButton
          type="button"
          className="w-full justify-center whitespace-nowrap px-2"
          onClick={() => setUsageOpen(true)}
          disabled={!index}
        >
          <ChartNoAxesColumn aria-hidden />
          LLM usage
        </ActionButton>
        <ToggleGroup
          legend="Retrieval index shown as"
          options={MODES}
          value={mode}
          onChange={setMode}
          fullWidth
        />
      </div>
      <div className="mt-3 min-w-0 overflow-hidden rounded-sm border border-border bg-background">
        {mode === "graph" ? (
          <AgenticFlow
            views={views}
            connections={INDEX_CONNECTIONS}
            positions={INDEX_POSITIONS}
            onOpenNode={openNode}
            onPlayNode={replayFromNode}
            replayDisabled={!canReplay}
            replayPending={replayNode.isPending}
            replayPendingNodeId={replayNodeId}
            ariaLabel="Agentic RAG graph"
            className="h-[min(76vh,760px)] min-h-[640px]"
            fitView
          />
        ) : (
          <div className="p-3">
            <AgenticNodeList
              rows={views}
              onOpen={openNode}
              onPlay={replayFromNode}
              replayDisabled={!canReplay}
              replayPending={replayNode.isPending}
              replayPendingNodeId={replayNodeId}
              label="Processed-book retrieval-index nodes"
            />
          </div>
        )}
      </div>
      {replayMessage ? (
        <p role={replayMessage.startsWith("Replay failed") ? "alert" : "status"} className="mt-3 text-sm">
          {replayMessage}
        </p>
      ) : null}

      <AgenticUsageSheet
        open={usageOpen}
        bookId={bookId}
        run={run}
        usageRunId={index?.agentic_run_id ?? null}
        live={live}
        source="retrieval-index"
        onClose={() => setUsageOpen(false)}
      />
      {indexInfoOpen ? (
        <SideSheet
          title="Agentic RAG details"
          subtitle={statusText}
          closeLabel="Close Agentic RAG details"
          onClose={() => setIndexInfoOpen(false)}
        >
          <section aria-label="Retrieval index status" className="rounded-sm border border-border bg-card p-4">
            <h3 className="text-sm font-medium">Index status</h3>
            <div className="mt-3 flex flex-col gap-3">
              <Metric label="Completed nodes" value={`${completedCount} / ${INDEX_NODES.length}`} />
              <Metric
                label="Processed pages"
                value={(index?.processed_page_count ?? run?.processed_page_count ?? 0).toLocaleString()}
              />
              <Metric label="Active stage" value={index?.current_node ? nodeLabel(index.current_node) : "—"} />
              <Metric
                label="Elasticsearch documents"
                value={(index?.elasticsearch_document_count ?? 0).toLocaleString()}
              />
              <Metric label="Qdrant points" value={(index?.qdrant_point_count ?? 0).toLocaleString()} />
              <Metric
                label="Neo4j nodes / relationships"
                value={`${(index?.neo4j_node_count ?? 0).toLocaleString()} / ${
                  (index?.neo4j_relationship_count ?? 0).toLocaleString()
                }`}
              />
              {index && index.graph_batches_total > 0 ? (
                <Metric
                  label="Graph extraction batches"
                  value={
                    `${index.graph_batches_completed.toLocaleString()} / ${index.graph_batches_total.toLocaleString()}`
                  }
                />
              ) : null}
              {indexOutdated ? <Metric label="Workflow" value="Update available" /> : null}
            </div>
          </section>
        </SideSheet>
      ) : null}
      {detailsOpen && selectedNode && selectedView && (
        <SideSheet
          key={selectedNode.id}
          title={selectedNode.label}
          subtitle={<AgenticStatusBadge status={selectedView.status} />}
          closeLabel="Close retrieval-index node details"
          onClose={() => setDetailsOpen(false)}
        >
          <Tabs defaultValue="overview" className="w-full">
            <TabsList aria-label="Retrieval-index node details">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="resources">Resources</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="flex flex-col gap-4 pt-4">
              <p className="text-sm">{selectedNode.detail}</p>
              <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
                <span>Reads</span>
                <AgenticPills items={selectedNode.reads} />
                <span>Writes</span>
                <AgenticPills items={selectedNode.writes} />
              </div>
              {selectedNodeId === "neo4j" && index && index.graph_batches_total > 0 ? (
                <Metric
                  label="Graph extraction batches"
                  value={`${index.graph_batches_completed.toLocaleString()} / ${index.graph_batches_total.toLocaleString()}`}
                />
              ) : null}
            </TabsContent>
            <TabsContent value="resources" className="pt-4">
              <AgenticNodeDetailsList resources={selectedNode.resources} />
            </TabsContent>
          </Tabs>
        </SideSheet>
      )}
    </section>
  );

  function openNode(nodeId: string) {
    if (!isRetrievalNodeId(nodeId)) return;
    setSelectedNodeId(nodeId);
    setDetailsOpen(true);
  }

  function replayFromNode(nodeId: string) {
    if (!isRetrievalNodeId(nodeId) || !index || replayNode.isPending || !canReplay) return;
    setReplayMessage(null);
    setReplayNodeId(nodeId);
    replayNode.mutate(
      { bookId, agenticRunId: index.agentic_run_id, nodeId },
      {
        onSuccess: () => {
          setReplayNodeId(null);
          setReplayMessage(`Replay queued from ${nodeLabel(nodeId)}.`);
        },
        onError: (error) => {
          setReplayNodeId(null);
          setReplayMessage(`Replay failed — ${apiErrorMessage(error)}`);
        },
      },
    );
  }
}

function nodeViews(index: RetrievalIndexStatus | null): AgenticNodeView[] {
  return INDEX_NODES.map((node) => {
    const status = nodeStatus(index, node.id);
    const kpi = nodeKpi(index, node.id, status);
    const liveLine = nodeLiveLine(index, node.id, status);
    const totalPages = index?.processed_page_count ?? 0;
    const pageProgress = PAGE_PROGRESS[node.id];
    const progress = index && pageProgress && totalPages > 0 && status !== "pending"
      ? {
          current: status === "done" ? totalPages : Math.min(index[pageProgress.field], totalPages),
          total: totalPages,
          label: pageProgress.label,
          active: 0,
        }
      : null;

    return {
      id: node.id,
      label: node.label,
      status,
      kpi,
      liveLine,
      resources: node.resources,
      progress,
    };
  });
}

function nodeStatus(index: RetrievalIndexStatus | null, nodeId: RetrievalNodeId): AgenticNodeStatus {
  if (index === null) return "pending";
  const status = index.node_status[nodeId];
  if (status) return status;
  if (index.status === "done") return "done";
  if (index.current_node === nodeId && index.status === "running") return "running";
  if (index.current_node === nodeId && index.status === "failed") return "failed";
  return "pending";
}

function nodeKpi(
  index: RetrievalIndexStatus | null,
  nodeId: RetrievalNodeId,
  status: AgenticNodeStatus,
): string | null {
  if (!index || status !== "done") return null;
  if (nodeId === "pin_sources") return `${index.processed_page_count.toLocaleString()} pages`;
  if (nodeId === "elasticsearch") return `${index.elasticsearch_document_count.toLocaleString()} documents`;
  if (nodeId === "qdrant") return `${index.qdrant_point_count.toLocaleString()} points`;
  if (nodeId === "neo4j") {
    return `${index.neo4j_node_count.toLocaleString()} nodes · ${index.neo4j_relationship_count.toLocaleString()} links`;
  }
  return "Index ready";
}

function nodeLiveLine(
  index: RetrievalIndexStatus | null,
  nodeId: RetrievalNodeId,
  status: AgenticNodeStatus,
): string | null {
  if (!index || status !== "running") return null;
  if (PAGE_PROGRESS[nodeId]) return null;
  return "Publishing index manifest";
}

function isRetrievalNodeId(nodeId: string): nodeId is RetrievalNodeId {
  return INDEX_NODES.some((node) => node.id === nodeId);
}

function nodeLabel(nodeId: string): string {
  return INDEX_NODES.find((node) => node.id === nodeId)?.label ?? nodeId;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-t border-border pt-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums text-foreground">{value}</span>
    </div>
  );
}
