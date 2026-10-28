"use client";

/** One stage's agent graph, drawn with React Flow: fixed positions (nothing moves when a status changes), a
 * button per node that opens its drawer, and — for synth — the refine loop as an amber arc that lights up when it
 * fires. Loaded lazily (`next/dynamic`), so the graph library stays out of every other page's JavaScript. */

import "@xyflow/react/dist/style.css";
import "./stage-flow.css";

import {
  BaseEdge,
  type Edge,
  type EdgeProps,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";

import {
  flowEdges,
  flowLayout,
  LOOP_REACH,
  loopIteration,
  NODE_HEIGHT,
  NODE_WIDTH,
} from "../domain/flow-layout";
import type { NodeStatus, StageNodeView } from "../domain/stages";
import type { RunStage } from "../params";
import { NODE_STATUS_LABEL, StatusGlyph } from "./stage-parts";

type StageNodeData = {
  label: string;
  status: NodeStatus;
  kpi: string | null;
  liveLine: string | null;
  hasLoopOut: boolean;
  hasLoopIn: boolean;
  onOpen: () => void;
};
type StageFlowNode = Node<StageNodeData, "stage">;

/** A node: a real button, so it is one tab stop with a name that carries its state in words. */
function StageNodeCard({ data }: NodeProps<StageFlowNode>) {
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
        } ${data.status === "future" ? "opacity-60" : ""}`}
      >
        <span className="flex items-center gap-1.5">
          <StatusGlyph status={data.status} />
          <span className="min-w-0 truncate text-sm">{data.label}</span>
          <span className="sr-only">— {NODE_STATUS_LABEL[data.status]}</span>
        </span>
        {data.kpi ? <span className="mt-0.5 truncate text-xs text-muted-foreground">{data.kpi}</span> : null}
        {data.liveLine ? (
          <span className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{data.liveLine}</span>
        ) : null}
      </button>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
      {data.hasLoopOut ? (
        <Handle id="loop-out" type="source" position={Position.Right} isConnectable={false} />
      ) : null}
      {data.hasLoopIn ? (
        <Handle id="loop-in" type="target" position={Position.Right} isConnectable={false} />
      ) : null}
    </>
  );
}

/** The refine loop: out of the right side of one node, round, and back into the right side of an earlier one. */
function RefineLoopEdge({ id, sourceX, sourceY, targetX, targetY, label, markerEnd }: EdgeProps) {
  const bow = LOOP_REACH * 1.3;
  const path = `M ${sourceX},${sourceY} C ${sourceX + bow},${sourceY} ${targetX + bow},${targetY} ${targetX},${targetY}`;
  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={markerEnd}
      label={label}
      labelX={sourceX + LOOP_REACH}
      labelY={(sourceY + targetY) / 2}
      labelBgPadding={[4, 2]}
    />
  );
}

// Stable identities: React Flow re-registers its types whenever these objects change.
const NODE_TYPES = { stage: StageNodeCard };
const EDGE_TYPES = { loop: RefineLoopEdge };

const ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14 } as const;

export function StageFlow({
  stage,
  title,
  views,
  kpis,
  liveLines,
  values,
  live,
  onOpen,
}: {
  stage: RunStage;
  title: string;
  views: StageNodeView[];
  kpis: Record<string, string>;
  liveLines: Record<string, string>;
  values: Record<string, unknown>;
  live: boolean;
  onOpen: (nodeId: string) => void;
}) {
  const ids = views.map((view) => view.node.id);
  const layout = flowLayout(stage, ids);
  const statusOf = new Map(views.map((view) => [view.node.id, view.status]));
  const iteration = loopIteration(values);

  const nodes: StageFlowNode[] = views.map((view) => ({
    id: view.node.id,
    type: "stage",
    position: layout.positions[view.node.id] ?? { x: 0, y: 0 },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    data: {
      label: view.node.label,
      status: view.status,
      kpi: kpis[view.node.id] ?? null,
      liveLine: liveLines[view.node.id] ?? null,
      hasLoopOut: layout.loop?.from === view.node.id,
      hasLoopIn: layout.loop?.to === view.node.id,
      onOpen: () => onOpen(view.node.id),
    },
  }));

  const edges: Edge[] = flowEdges(ids, layout.loop).map((edge) => {
    if (edge.kind === "loop") {
      const fired = iteration > 1;
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: "loop-out",
        targetHandle: "loop-in",
        type: "loop",
        label: fired ? `×${iteration} refine` : "refine loop",
        markerEnd: ARROW,
        focusable: false,
        className: `stage-edge-loop${fired ? " stage-edge-loop-fired" : ""}${fired && live ? " stage-edge-loop-firing" : ""}`,
      };
    }
    const active = statusOf.get(edge.source) === "done" && statusOf.get(edge.target) === "running";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      markerEnd: ARROW,
      focusable: false,
      className: active ? "stage-edge-active" : "",
    };
  });

  return (
    <figure
      className="stage-flow mx-auto max-w-full"
      style={{ width: layout.width, height: layout.height }}
      data-testid={`${stage}-flow`}
      aria-label={`${title} graph`}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
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
