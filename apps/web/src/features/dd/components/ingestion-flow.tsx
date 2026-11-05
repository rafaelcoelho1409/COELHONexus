"use client";

import "@xyflow/react/dist/style.css";
import "./ingestion-flow.css";

import { Handle, MarkerType, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";

import {
  type AgenticStage,
  INGESTION_NODES,
  type IngestionNodeStatus,
  ingestionNodeCaption,
  ingestionNodeStatus,
  ingestionNodeText,
} from "../domain/ingestion-progress";

type IngestionFlowData = {
  label: string;
  status: IngestionNodeStatus;
  caption: string | null;
  onOpen: () => void;
};
type IngestionFlowNode = Node<IngestionFlowData, "ingestion">;

const NODE_TYPES = { ingestion: IngestionNode };
const NODE_WIDTH = 256;
const NODE_HEIGHT = 70;
const NODE_GAP = 24;
const PADDING = 16;

function IngestionNode({ data }: NodeProps<IngestionFlowNode>) {
  const border =
    data.status === "running"
      ? "border-running"
      : data.status === "failed"
        ? "border-destructive"
        : "border-border";
  const text = ingestionNodeText(data.status);

  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <button
        type="button"
        onClick={data.onOpen}
        aria-haspopup="dialog"
        data-status={data.status}
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT, pointerEvents: "auto" }}
        className={`nodrag nopan flex flex-col justify-center overflow-hidden rounded-md border bg-card px-3 py-2 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${border}`}
      >
        <span className="flex items-center justify-between gap-3">
          <span className="min-w-0 truncate text-sm font-medium">{data.label}</span>
          <span className="shrink-0 text-xs text-muted-foreground">{text}</span>
          <span className="sr-only">— {text}</span>
        </span>
        {data.caption ? (
          <span className="mt-1 truncate text-xs text-muted-foreground">{data.caption}</span>
        ) : null}
      </button>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </>
  );
}

export function IngestionFlow({
  agentic,
  runStatus,
  onOpenNode,
}: {
  agentic: AgenticStage | null;
  runStatus: string | null;
  onOpenNode: (nodeId: string) => void;
}) {
  const nodes: IngestionFlowNode[] = INGESTION_NODES.map((node, index) => ({
    id: node.id,
    type: "ingestion",
    position: { x: PADDING, y: PADDING + index * (NODE_HEIGHT + NODE_GAP) },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    data: {
      label: node.label,
      status: ingestionNodeStatus(agentic, node.id, runStatus),
      caption: ingestionNodeCaption(agentic, node.id),
      onOpen: () => onOpenNode(node.id),
    },
  }));
  const edges = INGESTION_NODES.slice(0, -1).map((node, index) => ({
    id: `${node.id}->${INGESTION_NODES[index + 1]?.id ?? "end"}`,
    source: node.id,
    target: INGESTION_NODES[index + 1]?.id ?? "",
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    focusable: false,
  }));

  return (
    <figure
      className="dd-ingestion-flow mx-auto max-w-full"
      style={{
        width: NODE_WIDTH + PADDING * 2,
        height:
          PADDING * 2 + INGESTION_NODES.length * NODE_HEIGHT + (INGESTION_NODES.length - 1) * NODE_GAP + 28,
      }}
      aria-label="Docs Distiller agentic ingestion graph"
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        defaultViewport={{ x: 0, y: 0, zoom: 1 }}
        minZoom={1}
        maxZoom={1}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: false }}
      />
    </figure>
  );
}
