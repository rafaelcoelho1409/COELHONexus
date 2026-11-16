"use client";

/** The Agentic AI graph, drawn with React Flow the way the Docs Distiller draws a stage: fixed positions (nothing
 * moves when a status changes), a button per node that opens its sheet, a separate replay action, a glyph for the
 * state, a headline number once the node is done and a live line while it runs. The edge into the node now running
 * is highlighted. Loaded
 * lazily (`next/dynamic`), so the graph library stays out of every other page's JavaScript. */

import "@xyflow/react/dist/style.css";
import "./agentic-flow.css";

import { Controls, type Edge, Handle, MarkerType, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";

import type { AgenticNodeStatus, AgenticNodeView } from "../domain/agentic-progress";
import {
  AGENTIC_STATUS_LABEL,
  AgenticNodeProgressBar,
  AgenticReplayButton,
  AgenticStatusGlyph,
} from "./agentic-parts";

/** Compact nodes leave operational resources to the node side view; progress remains in the node. */
const NODE_WIDTH = 216;
const NODE_HEIGHT = 136;
const NODE_GAP = 30;
const PADDING = 16;
type AgenticFlowData = {
  nodeId: string;
  label: string;
  status: AgenticNodeStatus;
  kpi: string | null;
  liveLine: string | null;
  progress: AgenticNodeView["progress"];
  onOpen: () => void;
  onPlay?: () => void;
  playPending: boolean;
  replayPending: boolean;
};
type AgenticFlowNode = Node<AgenticFlowData, "agentic">;

/** The title and metrics remain clickable; replay has its own header cell with no overlapping hit area. */
function AgenticNodeCard({ data }: NodeProps<AgenticFlowNode>) {
  const borderTone =
    data.status === "running"
      ? "border-running"
      : data.status === "failed"
        ? "border-destructive"
        : "border-border";

  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div
        data-node-id={data.nodeId}
        className={`flex h-full w-full flex-col overflow-hidden rounded-md border bg-card ${borderTone}`}
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
      >
        {data.onPlay ? (
          <div className="flex min-h-9 items-center gap-1 border-b border-border/60 px-1.5">
            <button
              type="button"
              onClick={data.onOpen}
              aria-label={`Open ${data.label} node details — ${AGENTIC_STATUS_LABEL[data.status]}`}
              aria-haspopup="dialog"
              className="nodrag nopan pointer-events-auto flex min-w-0 flex-1 items-center gap-1.5 rounded-sm px-1 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
            >
              <AgenticStatusGlyph status={data.status} />
              <span className="min-w-0 truncate text-sm">{data.label}</span>
              <span className="sr-only">— {AGENTIC_STATUS_LABEL[data.status]}</span>
            </button>
            <AgenticReplayButton
              label={data.label}
              onPlay={data.onPlay}
              pending={data.playPending}
              disabled={data.replayPending}
            />
          </div>
        ) : null}
        <button
          type="button"
          data-status={data.status}
          onClick={data.onOpen}
          aria-label={
            data.onPlay
              ? `Open ${data.label} node progress details`
              : `Open ${data.label} node details — ${AGENTIC_STATUS_LABEL[data.status]}`
          }
          aria-haspopup="dialog"
          className={`nodrag nopan pointer-events-auto flex min-h-0 min-w-0 flex-1 flex-col justify-start overflow-hidden text-left hover:bg-muted focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-ring ${
            data.onPlay ? "px-3 py-2" : "h-full rounded-md px-3 py-2.5"
          }`}
        >
          <span className="flex min-w-0 flex-col gap-y-1">
            {!data.onPlay ? (
              <span className="flex items-center gap-1.5">
                <AgenticStatusGlyph status={data.status} />
                <span className="min-w-0 truncate text-sm">{data.label}</span>
                <span className="sr-only">— {AGENTIC_STATUS_LABEL[data.status]}</span>
              </span>
            ) : null}
            <span className="truncate font-mono text-xs text-muted-foreground">{data.nodeId}</span>
            {data.kpi ? <span className="truncate text-xs text-muted-foreground">{data.kpi}</span> : null}
            {data.liveLine ? (
              <span className="truncate font-mono text-xs text-muted-foreground">{data.liveLine}</span>
            ) : null}
            {data.progress ? (
              <span className="mt-auto block">
                <AgenticNodeProgressBar label={data.label} progress={data.progress} />
              </span>
            ) : null}
          </span>
        </button>
      </div>
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
  onPlayNode,
  replayPending = false,
  replayPendingNodeId = null,
  ariaLabel = "Book Distiller agentic ingestion graph",
}: {
  views: readonly AgenticNodeView[];
  onOpenNode: (nodeId: string) => void;
  onPlayNode?: (nodeId: string) => void;
  replayPending?: boolean;
  replayPendingNodeId?: string | null;
  ariaLabel?: string;
}) {
  const nodes: AgenticFlowNode[] = views.map((view, index) => ({
    id: view.id,
    type: "agentic",
    position: { x: PADDING, y: PADDING + index * (NODE_HEIGHT + NODE_GAP) },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    data: {
      nodeId: view.id,
      label: view.label,
      status: view.status,
      kpi: view.kpi,
      liveLine: view.liveLine,
      progress: view.progress,
      onOpen: () => onOpenNode(view.id),
      ...(onPlayNode ? { onPlay: () => onPlayNode(view.id) } : {}),
      playPending: replayPending && replayPendingNodeId === view.id,
      replayPending,
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
    <figure className="bd-agentic-flow mx-auto h-[min(76vh,880px)] min-h-[440px] w-full min-w-0" aria-label={ariaLabel}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        defaultViewport={{ x: 0, y: 0, zoom: 0.82 }}
        minZoom={0.18}
        maxZoom={2.25}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        zoomOnDoubleClick={false}
        preventScrolling
        proOptions={{ hideAttribution: false }}
      >
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </figure>
  );
}
