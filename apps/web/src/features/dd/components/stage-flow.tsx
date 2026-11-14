"use client";

/** One agent graph (planner, synth or Agentic AI ingestion), drawn with React Flow: fixed positions (nothing moves when a status changes), a
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
  type FlowEdgeSpec,
  type FlowStage,
  flowEdges,
  flowLayout,
  LOOP_REACH,
  loopIteration,
  NODE_HEIGHT,
  NODE_WIDTH,
} from "../domain/flow-layout";
import type { NodeStatus, NodeView } from "../domain/stages";
import { NODE_STATUS_LABEL, NodeDetailsList, type StageNodeDetails, StatusGlyph } from "./stage-parts";

type StageNodeData = {
  id: string;
  stage: FlowStage;
  label: string;
  status: NodeStatus;
  kpi: string | null;
  liveLine: string | null;
  details: StageFlowNodeDetails | null;
  width: number;
  height: number;
  hasLoopOut: boolean;
  hasLoopIn: boolean;
  onOpen: () => void;
};
type StageFlowNode = Node<StageNodeData, "stage">;

export type StageFlowNodeDetails = StageNodeDetails & {
  progress: {
    current: number;
    total: number;
    percent: number;
    label: string;
  } | null;
};

/** A node: a real button, so it is one tab stop with a name that carries its state in words. */
function StageNodeCard({ data }: NodeProps<StageFlowNode>) {
  if (data.details) {
    const border =
      data.status === "running"
        ? "border-running"
        : data.status === "failed"
          ? "border-destructive"
          : data.status === "needs_review"
            ? "border-warning"
            : "border-border";
    const progress = data.details.progress;

    return (
      <>
        <Handle type="target" position={Position.Top} isConnectable={false} />
        <fieldset
          aria-label={`${data.label} — ${NODE_STATUS_LABEL[data.status]}`}
          data-status={data.status}
          style={{ width: data.width, height: data.height, pointerEvents: "none" }}
          className={`relative m-0 flex min-w-0 flex-col overflow-hidden rounded-md border bg-card p-0 hover:bg-muted ${border} ${
            data.status === "future" ? "opacity-60" : ""
          }`}
        >
          <legend className="sr-only">{`${data.label} — ${NODE_STATUS_LABEL[data.status]}`}</legend>
          <div
            id={`dd-${data.stage}-node-${data.id}-details`}
            className="pointer-events-none flex min-h-0 flex-1 flex-col"
          >
            <span className="flex min-h-9 w-full items-start gap-2 px-3 pt-2">
              <StatusGlyph status={data.status} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm leading-4">{data.label}</span>
                <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{data.id}</span>
              </span>
              <span className="sr-only">— {NODE_STATUS_LABEL[data.status]}</span>
            </span>
            {data.stage !== "ingestion" && data.kpi ? (
              <span className="px-3 pt-1 text-xs tabular-nums text-muted-foreground">{data.kpi}</span>
            ) : null}
            {data.stage !== "ingestion" && data.liveLine ? (
              <span className="px-3 pt-1 font-mono text-xs break-all text-muted-foreground">
                {data.liveLine}
              </span>
            ) : null}
            <NodeDetailsList details={data.details} compact />
            {progress ? (
              <span className="mt-auto block px-3 pb-2 pt-1">
                <span className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-muted-foreground">{progress.label}</span>
                  <span className="shrink-0 tabular-nums">
                    {progress.total > 0 ? `${progress.current} of ${progress.total}` : "Waiting for pages"}
                  </span>
                </span>
                <div
                  role="progressbar"
                  aria-label={`${data.label} ${progress.label} progress`}
                  aria-valuemin={progress.total > 0 ? 0 : undefined}
                  aria-valuemax={progress.total > 0 ? progress.total : undefined}
                  aria-valuenow={progress.total > 0 ? progress.current : undefined}
                  aria-valuetext={
                    progress.total > 0
                      ? `${progress.current} of ${progress.total} ${progress.label}`
                      : "Waiting for pages"
                  }
                  className="mt-1 h-1 w-full overflow-hidden rounded-sm bg-muted"
                >
                  <div className="h-full bg-primary" style={{ width: `${progress.percent}%` }} />
                </div>
              </span>
            ) : null}
          </div>
          <button
            type="button"
            onClick={data.onOpen}
            aria-label={`Open ${data.label} usage`}
            aria-describedby={`dd-${data.stage}-node-${data.id}-details`}
            aria-haspopup="dialog"
            style={{ pointerEvents: "auto" }}
            className="nodrag nopan absolute inset-0 z-10 cursor-pointer rounded-md border-0 bg-transparent p-0 text-transparent hover:bg-transparent focus-visible:outline-2 focus-visible:outline-ring"
          />
        </fieldset>
        <Handle type="source" position={Position.Bottom} isConnectable={false} />
      </>
    );
  }

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
        style={{ width: data.width, height: data.height, pointerEvents: "auto" }}
        // `overflow-hidden` is the guard: whatever a line holds, nothing paints outside the node's shape (every line also truncates).
        className={`nodrag nopan flex flex-col justify-center overflow-hidden rounded-md border bg-card px-3 py-2.5 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
          data.status === "running"
            ? "border-running"
            : data.status === "failed"
              ? "border-destructive"
              : data.status === "needs_review"
                ? "border-warning"
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
  edges: topologyEdges,
  nodeDetails = {},
}: {
  stage: FlowStage;
  title: string;
  views: NodeView[];
  kpis: Record<string, string>;
  liveLines: Record<string, string>;
  values: Record<string, unknown>;
  live: boolean;
  onOpen: (nodeId: string) => void;
  /** Connections from LangGraph's compiled topology, when the server supplied it. */
  edges?: readonly FlowEdgeSpec[];
  /** Extra operational context for nodes that need richer cards. */
  nodeDetails?: Readonly<Record<string, StageFlowNodeDetails>>;
}) {
  const ids = views.map((view) => view.node.id);
  const hasNodeDetails = views.some((view) => nodeDetails[view.node.id]);
  const nodeWidth = stage === "ingestion" || hasNodeDetails ? 304 : NODE_WIDTH;
  const nodeHeight = stage === "ingestion" || hasNodeDetails ? 168 : NODE_HEIGHT;
  const layout = flowLayout(stage, ids, { nodeWidth, nodeHeight });
  const statusOf = new Map(views.map((view) => [view.node.id, view.status]));
  const iteration = loopIteration(values);

  const nodes: StageFlowNode[] = views.map((view) => ({
    id: view.node.id,
    type: "stage",
    position: layout.positions[view.node.id] ?? { x: 0, y: 0 },
    style: { width: nodeWidth, height: nodeHeight },
    data: {
      id: view.node.id,
      stage,
      label: view.node.label,
      status: view.status,
      kpi: kpis[view.node.id] ?? null,
      liveLine: liveLines[view.node.id] ?? null,
      details: nodeDetails[view.node.id] ?? null,
      width: nodeWidth,
      height: nodeHeight,
      hasLoopOut: layout.loop?.from === view.node.id,
      hasLoopIn: layout.loop?.to === view.node.id,
      onOpen: () => onOpen(view.node.id),
    },
  }));

  const edges: Edge[] = (topologyEdges ?? flowEdges(ids, layout.loop)).map((edge) => {
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
