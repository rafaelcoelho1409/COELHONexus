"use client";

/** The Agentic AI graph, drawn with React Flow the way the Docs Distiller draws a stage: fixed positions (nothing
 * moves when a status changes), one button per node that opens its sheet, a glyph for the state, a headline number
 * once the node is done and a live line while it runs. The edge into the node now running is highlighted. Loaded
 * lazily (`next/dynamic`), so the graph library stays out of every other page's JavaScript. */

import "@xyflow/react/dist/style.css";
import "./agentic-flow.css";

import { type Edge, Handle, MarkerType, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";

import type { AgenticNodeStatus, AgenticNodeView } from "../domain/agentic-progress";
import { AGENTIC_STATUS_LABEL, AgenticStatusGlyph } from "./agentic-parts";

/** A node can show three lines — its label (a 20px line), a headline number and a live line (18px each), 2px apart —
 * so the height keeps 10px of air above and below them. */
const NODE_WIDTH = 216;
const NODE_HEIGHT = 80;
const NODE_GAP = 30;
const PADDING = 16;
/** A strip under the last node for React Flow's small credit line, so it never covers a node. */
const CREDIT_ROOM = 32;

type AgenticFlowData = {
  label: string;
  status: AgenticNodeStatus;
  kpi: string | null;
  liveLine: string | null;
  onOpen: () => void;
};
type AgenticFlowNode = Node<AgenticFlowData, "agentic">;

/** A node: a real button, so it is one tab stop with a name that carries its state in words. */
function AgenticNodeCard({ data }: NodeProps<AgenticFlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <button
        type="button"
        onClick={data.onOpen}
        aria-haspopup="dialog"
        data-status={data.status}
        // React Flow switches pointer events off for nodes that cannot be dragged or selected, and children
        // inherit that; the button is the one thing here that must stay clickable.
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT, pointerEvents: "auto" }}
        // `overflow-hidden` is the guard: whatever a line holds, nothing paints outside the node's shape (every line also truncates).
        className={`nodrag nopan flex flex-col justify-center overflow-hidden rounded-md border bg-card px-3 py-2.5 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
          data.status === "running"
            ? "border-running"
            : data.status === "failed"
              ? "border-destructive"
              : "border-border"
        }`}
      >
        <span className="flex items-center gap-1.5">
          <AgenticStatusGlyph status={data.status} />
          <span className="min-w-0 truncate text-sm">{data.label}</span>
          <span className="sr-only">— {AGENTIC_STATUS_LABEL[data.status]}</span>
        </span>
        {data.kpi ? <span className="mt-0.5 truncate text-xs text-muted-foreground">{data.kpi}</span> : null}
        {data.liveLine ? (
          <span className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{data.liveLine}</span>
        ) : null}
      </button>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </>
  );
}

// Stable identity: React Flow re-registers its types whenever this object changes.
const NODE_TYPES = { agentic: AgenticNodeCard };

const ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14 } as const;

export function AgenticFlow({
  views,
  onOpenNode,
}: {
  views: readonly AgenticNodeView[];
  onOpenNode: (nodeId: string) => void;
}) {
  const nodes: AgenticFlowNode[] = views.map((view, index) => ({
    id: view.id,
    type: "agentic",
    position: { x: PADDING, y: PADDING + index * (NODE_HEIGHT + NODE_GAP) },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    data: {
      label: view.label,
      status: view.status,
      kpi: view.kpi,
      liveLine: view.liveLine,
      onOpen: () => onOpenNode(view.id),
    },
  }));
  const edges: Edge[] = views.slice(0, -1).flatMap((view, index) => {
    const next = views[index + 1];
    if (!next) return [];
    const active = view.status === "done" && next.status === "running";
    return [
      {
        id: `${view.id}->${next.id}`,
        source: view.id,
        target: next.id,
        markerEnd: ARROW,
        focusable: false,
        className: active ? "agentic-edge-active" : "",
      },
    ];
  });

  return (
    <figure
      className="bd-agentic-flow mx-auto max-w-full"
      style={{
        width: NODE_WIDTH + PADDING * 2,
        height:
          PADDING * 2 + views.length * NODE_HEIGHT + Math.max(0, views.length - 1) * NODE_GAP + CREDIT_ROOM,
      }}
      aria-label="Book Distiller agentic ingestion graph"
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
