"use client";

/** Read-only view of the compiled YCS Ask adaptive LangGraph. */

import "@xyflow/react/dist/style.css";
import "./ask-agentic-flow.css";

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

import { ReactFlowNodeInternals } from "@/shared/lib/react-flow-node-internals";

import type { AskAgenticNode, AskAgenticNodeStatus } from "./ask-agentic-types";
import { StatusBadge } from "./status-badge";

const FLOW_WIDTH = 280;
const FLOW_HEIGHT = 620;
const NODE_WIDTH = 120;
const NODE_HEIGHT = 68;
const END_WIDTH = 56;
const END_HEIGHT = 32;

const POSITIONS: Record<string, { x: number; y: number; width?: number }> = {
  prepare: { x: 8, y: 8, width: 248 },
  direct_answer: { x: 8, y: 100 },
  run_standard: { x: 144, y: 100 },
  plan_research: { x: 144, y: 232 },
  run_subagents: { x: 144, y: 310 },
  synthesize: { x: 144, y: 388 },
  critic: { x: 144, y: 466 },
};

const END_POSITIONS = {
  end_conversation: { x: 40, y: 184, label: "END: direct answer completed" },
  end_standard: { x: 176, y: 184, label: "END: standard video RAG completed" },
  end_deep: { x: 176, y: 550, label: "END: deep research completed" },
} as const;

const FLOW_LABELS: Readonly<Record<string, string>> = {
  prepare: "Prepare & route",
  direct_answer: "Direct answer",
  run_standard: "Video RAG",
  plan_research: "Plan research",
  run_subagents: "Research agents",
  synthesize: "Synthesize",
  critic: "Review answer",
};

const STATUS: Record<
  AskAgenticNodeStatus,
  { tone: "queued" | "running" | "done" | "failed" | "na"; label: string }
> = {
  queued: { tone: "queued", label: "Queued" },
  active: { tone: "running", label: "Running" },
  done: { tone: "done", label: "Done" },
  failed: { tone: "failed", label: "Failed" },
  skipped: { tone: "na", label: "Not selected" },
};

type AskFlowNodeData = {
  id: string;
  label: string;
  accessibleLabel: string;
  status: AskAgenticNodeStatus;
  width: number;
  onOpen: () => void;
};

type AskFlowNode = Node<AskFlowNodeData, "ask-agentic">;
type AskFlowEndData = { label: string };
type AskFlowEndNode = Node<AskFlowEndData, "ask-agentic-end">;
type AskFlowGraphNode = AskFlowNode | AskFlowEndNode;

function AskFlowNodeCard({ data }: NodeProps<AskFlowNode>) {
  const state = STATUS[data.status];
  const border =
    data.status === "active"
      ? "border-running"
      : data.status === "failed"
        ? "border-destructive"
        : "border-border";

  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      {data.id === "plan_research" ? (
        <Handle id="deep-in" type="target" position={Position.Right} isConnectable={false} />
      ) : null}
      {data.id === "run_standard" ? (
        <Handle id="fallback-in" type="target" position={Position.Left} isConnectable={false} />
      ) : null}
      <fieldset
        aria-label={data.accessibleLabel + " — " + state.label}
        data-status={data.status}
        style={{ width: data.width, height: NODE_HEIGHT, pointerEvents: "none" }}
        className={
          "relative isolate m-0 flex min-w-0 flex-col overflow-hidden rounded-md border bg-card p-0 " +
          border +
          (data.status === "skipped" ? " opacity-65" : "")
        }
      >
        <legend className="sr-only">{data.accessibleLabel + " — " + state.label}</legend>
        <button
          type="button"
          onClick={data.onOpen}
          aria-label={"Open " + data.accessibleLabel + " node details — " + state.label}
          aria-haspopup="dialog"
          style={{ pointerEvents: "auto" }}
          className="nodrag nopan absolute inset-0 z-10 flex min-h-0 flex-col items-start justify-center overflow-hidden rounded-md border-0 bg-transparent px-2 py-1 text-left text-foreground hover:bg-muted/20 focus-visible:outline-2 focus-visible:outline-ring"
        >
          <span className="line-clamp-2 text-xs font-medium leading-4">{data.label}</span>
          <StatusBadge tone={state.tone} label={state.label} className="mt-0.5 gap-1 text-xs" />
        </button>
      </fieldset>
      {data.id === "prepare" ? (
        <Handle id="deep-out" type="source" position={Position.Right} isConnectable={false} />
      ) : null}
      {data.id === "direct_answer" ? (
        <Handle id="fallback-out" type="source" position={Position.Right} isConnectable={false} />
      ) : null}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </>
  );
}

function AskFlowEndCard({ data }: NodeProps<AskFlowEndNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div
        role="img"
        aria-label={data.label}
        style={{ width: END_WIDTH, height: END_HEIGHT }}
        className="flex items-center justify-center rounded-full border border-dashed border-border bg-muted text-xs font-medium text-muted-foreground"
      >
        END
      </div>
    </>
  );
}

function FallbackEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd }: EdgeProps) {
  const path =
    "M " +
    sourceX +
    "," +
    sourceY +
    " C " +
    (sourceX + 24) +
    "," +
    sourceY +
    " " +
    (targetX - 24) +
    "," +
    targetY +
    " " +
    targetX +
    "," +
    targetY;
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} />;
}

function DeepRouteEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd }: EdgeProps) {
  const gutter = FLOW_WIDTH - 8;
  const path =
    "M " +
    sourceX +
    "," +
    sourceY +
    " L " +
    gutter +
    "," +
    sourceY +
    " L " +
    gutter +
    "," +
    targetY +
    " L " +
    targetX +
    "," +
    targetY;
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} />;
}

const NODE_TYPES = {
  "ask-agentic": AskFlowNodeCard,
  "ask-agentic-end": AskFlowEndCard,
};
const EDGE_TYPES = { fallback: FallbackEdge, "deep-route": DeepRouteEdge };
const ARROW = { type: MarkerType.ArrowClosed, width: 12, height: 12 } as const;

const LINKS: ReadonlyArray<{
  source: string;
  target: string;
  kind: "route" | "flow" | "fallback" | "deep-route" | "end";
}> = [
  { source: "prepare", target: "direct_answer", kind: "route" },
  { source: "prepare", target: "run_standard", kind: "route" },
  { source: "prepare", target: "plan_research", kind: "deep-route" },
  { source: "direct_answer", target: "run_standard", kind: "fallback" },
  { source: "plan_research", target: "run_subagents", kind: "flow" },
  { source: "run_subagents", target: "synthesize", kind: "flow" },
  { source: "synthesize", target: "critic", kind: "flow" },
  { source: "direct_answer", target: "end_conversation", kind: "end" },
  { source: "run_standard", target: "end_standard", kind: "end" },
  { source: "critic", target: "end_deep", kind: "end" },
];

export function AskAgenticFlow({
  nodes,
  onOpen,
}: {
  nodes: readonly AskAgenticNode[];
  onOpen: (nodeId: string) => void;
}) {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const flowNodes: AskFlowGraphNode[] = nodes.map((node) => {
    const position = POSITIONS[node.id] ?? { x: 8, y: 8, width: NODE_WIDTH };
    return {
      id: node.id,
      type: "ask-agentic",
      position: { x: position.x, y: position.y },
      width: position.width ?? NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        id: node.id,
        label: FLOW_LABELS[node.id] ?? node.label,
        accessibleLabel: node.label,
        status: node.status,
        width: position.width ?? NODE_WIDTH,
        onOpen: () => onOpen(node.id),
      },
    };
  });

  for (const [id, position] of Object.entries(END_POSITIONS)) {
    flowNodes.push({
      id,
      type: "ask-agentic-end",
      position: { x: position.x, y: position.y },
      width: END_WIDTH,
      height: END_HEIGHT,
      data: { label: position.label },
    });
  }

  const edges: Edge[] = LINKS.flatMap(({ source, target, kind }) => {
    const sourceNode = nodeById.get(source);
    const targetNode = nodeById.get(target);
    if (!sourceNode || (!targetNode && kind !== "end")) return [];
    const active = sourceNode.status === "done" && targetNode?.status === "active";
    const className = active
      ? "ask-agentic-edge-active"
      : kind === "route" || kind === "deep-route"
        ? "ask-agentic-edge-branch"
        : kind === "fallback"
          ? "ask-agentic-edge-fallback"
          : kind === "end"
            ? "ask-agentic-edge-terminal"
            : "";

    return [
      {
        id: source + "->" + target,
        source,
        target,
        ...(kind === "fallback"
          ? {
              type: "fallback",
              sourceHandle: "fallback-out",
              targetHandle: "fallback-in",
            }
          : {}),
        ...(kind === "deep-route"
          ? {
              type: "deep-route",
              sourceHandle: "deep-out",
              targetHandle: "deep-in",
            }
          : {}),
        markerEnd: ARROW,
        focusable: false,
        className,
      },
    ];
  });

  return (
    <figure
      className="ycs-ask-flow mx-auto"
      style={{ width: FLOW_WIDTH, height: FLOW_HEIGHT }}
      aria-label="YCS Ask LangGraph: prepare and route the question, then run the conversation, standard video RAG, or deep research path. Terminal paths end at END."
    >
      <ReactFlow
        nodes={flowNodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        defaultViewport={{ x: 0, y: 0, zoom: 1 }}
        minZoom={1}
        maxZoom={1}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        proOptions={{ hideAttribution: false }}
      >
        <ReactFlowNodeInternals nodeIds={flowNodes.map((node) => node.id)} revision={flowNodes} />
      </ReactFlow>
    </figure>
  );
}
