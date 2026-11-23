"use client";

/** Read-only view of the YCS Ask adaptive LangGraph. */

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
import { Info } from "lucide-react";

import { ReactFlowNodeInternals } from "@/shared/lib/react-flow-node-internals";

import type { AskAgenticNode, AskAgenticNodeStatus } from "./ask-agentic-types";
import { StatusBadge } from "./status-badge";

const FLOW_HEIGHT = 1320;
const FLOW_RIGHT_GUTTER = 472;
const NODE_GAP = 24;
const NODE_WIDTH = 216;
const NODE_HEIGHT = 92;
const END_WIDTH = 56;
const END_HEIGHT = 32;

const POSITIONS: Readonly<Record<string, { x: number; y: number; width?: number }>> = {
  prepare: { x: 8, y: 16, width: 448 },
  direct_answer: { x: 8, y: 148 },
  retrieve: { x: 240, y: 148 },
  grade_documents: { x: 240, y: 292 },
  rewrite_query: { x: 8, y: 292 },
  fallback_answer: { x: 8, y: 440 },
  generate: { x: 240, y: 440 },
  check_hallucination: { x: 240, y: 584 },
  corroborate: { x: 8, y: 584 },
  format_citations: { x: 240, y: 728 },
  plan_research: { x: 8, y: 728 },
  run_subagents: { x: 240, y: 872 },
  synthesize: { x: 8, y: 1016 },
  critic: { x: 240, y: 1160 },
};

const END_POSITIONS = {
  end_conversation: { x: 88, y: 264, label: "END · conversation" },
  end_fallback: { x: 88, y: 552, label: "END · limited evidence" },
  end_standard: { x: 320, y: 840, label: "END · standard" },
  end_deep: { x: 320, y: 1272, label: "END · deep research" },
} as const;

const FLOW_LABELS: Readonly<Record<string, string>> = {
  prepare: "Prepare and route question",
  direct_answer: "Conversation reply",
  retrieve: "Retrieve transcript evidence",
  grade_documents: "Grade transcript evidence",
  rewrite_query: "Refine the retrieval query",
  fallback_answer: "Answer with limited evidence",
  generate: "Generate a grounded answer",
  check_hallucination: "Verify transcript support",
  corroborate: "Corroborate an uncertain answer",
  format_citations: "Format video citations",
  plan_research: "Plan focused research",
  run_subagents: "Research sub-questions",
  synthesize: "Synthesize findings",
  critic: "Review answer support",
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
  activity: string | undefined;
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
      <Handle id="target-top" type="target" position={Position.Top} isConnectable={false} />
      <Handle
        id="target-top-left"
        type="target"
        position={Position.Top}
        style={{ left: "35%" }}
        isConnectable={false}
      />
      <Handle
        id="target-top-right"
        type="target"
        position={Position.Top}
        style={{ left: "65%" }}
        isConnectable={false}
      />
      <Handle
        id="target-bottom-left"
        type="target"
        position={Position.Bottom}
        style={{ left: "35%" }}
        isConnectable={false}
      />
      <Handle id="target-left" type="target" position={Position.Left} isConnectable={false} />
      <Handle id="target-right" type="target" position={Position.Right} isConnectable={false} />
      <fieldset
        aria-label={`${data.accessibleLabel} — ${state.label}`}
        data-status={data.status}
        style={{ width: data.width, height: NODE_HEIGHT, pointerEvents: "none" }}
        className={`relative isolate m-0 flex min-w-0 flex-col overflow-hidden rounded-md border bg-card p-0 ${border}${data.status === "skipped" ? " opacity-65" : ""}`}
      >
        <legend className="sr-only">{`${data.accessibleLabel} — ${state.label}`}</legend>
        <button
          type="button"
          onClick={data.onOpen}
          aria-label={`Open ${data.accessibleLabel} node details — ${state.label}`}
          aria-haspopup="dialog"
          style={{ pointerEvents: "auto" }}
          className="nodrag nopan absolute inset-0 z-10 flex min-h-0 flex-col items-start justify-center overflow-hidden rounded-md border-0 bg-transparent px-3 py-2 text-left text-foreground hover:bg-muted/20 focus-visible:outline-2 focus-visible:outline-ring"
        >
          <span className="flex min-w-0 items-center gap-1.5 text-xs font-medium leading-4">
            <span className="truncate">{data.label}</span>
            <Info aria-hidden className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
          </span>
          <span className="mt-0.5 font-mono text-xs text-muted-foreground">{data.id}</span>
          <StatusBadge tone={state.tone} label={state.label} className="mt-0.5 gap-1 text-xs" />
          {data.activity ? (
            <span className="mt-1 w-full truncate text-xs text-muted-foreground">{data.activity}</span>
          ) : null}
        </button>
      </fieldset>
      <Handle id="source-bottom" type="source" position={Position.Bottom} isConnectable={false} />
      <Handle
        id="source-bottom-left"
        type="source"
        position={Position.Bottom}
        style={{ left: "35%" }}
        isConnectable={false}
      />
      <Handle
        id="source-bottom-right"
        type="source"
        position={Position.Bottom}
        style={{ left: "65%" }}
        isConnectable={false}
      />
      <Handle id="source-left" type="source" position={Position.Left} isConnectable={false} />
      <Handle id="source-right" type="source" position={Position.Right} isConnectable={false} />
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

function DeepRouteEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style }: EdgeProps) {
  const gutter = -16;
  const path = `M ${sourceX},${sourceY} H ${gutter} V ${targetY} H ${targetX}`;
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />;
}

function FeedbackEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style }: EdgeProps) {
  const outsideX = 0;
  const bypassY = targetY + NODE_GAP / 2;
  const path = `M ${sourceX},${sourceY} H ${outsideX} V ${bypassY} H ${targetX} V ${targetY}`;
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />;
}

function HallucinationRetryEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style }: EdgeProps) {
  const outsideX = FLOW_RIGHT_GUTTER;
  const bypassY = targetY - NODE_GAP / 2;
  const path = `M ${sourceX},${sourceY} H ${outsideX} V ${bypassY} H ${targetX} V ${targetY}`;
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />;
}

const NODE_TYPES = {
  "ask-agentic": AskFlowNodeCard,
  "ask-agentic-end": AskFlowEndCard,
};
const EDGE_TYPES = {
  "deep-route": DeepRouteEdge,
  feedback: FeedbackEdge,
  "feedback-right": HallucinationRetryEdge,
};
const ARROW = { type: MarkerType.ArrowClosed, width: 12, height: 12 } as const;

const LINKS: ReadonlyArray<{
  source: string;
  target: string;
  kind: "route" | "flow" | "fallback" | "deep-route" | "feedback" | "feedback-right" | "end";
  sourceHandle?: string;
  targetHandle?: string;
}> = [
  {
    source: "prepare",
    target: "direct_answer",
    kind: "route",
    sourceHandle: "source-bottom-left",
    targetHandle: "target-top",
  },
  {
    source: "prepare",
    target: "retrieve",
    kind: "route",
    sourceHandle: "source-bottom-right",
    targetHandle: "target-top",
  },
  {
    source: "prepare",
    target: "plan_research",
    kind: "deep-route",
    sourceHandle: "source-left",
    targetHandle: "target-left",
  },
  {
    source: "direct_answer",
    target: "retrieve",
    kind: "fallback",
    sourceHandle: "source-right",
    targetHandle: "target-left",
  },
  { source: "retrieve", target: "grade_documents", kind: "flow" },
  {
    source: "grade_documents",
    target: "rewrite_query",
    kind: "flow",
    sourceHandle: "source-left",
    targetHandle: "target-right",
  },
  { source: "grade_documents", target: "generate", kind: "flow" },
  {
    source: "grade_documents",
    target: "fallback_answer",
    kind: "fallback",
    sourceHandle: "source-bottom-left",
    targetHandle: "target-top-right",
  },
  {
    source: "rewrite_query",
    target: "retrieve",
    kind: "feedback",
    sourceHandle: "source-left",
    targetHandle: "target-bottom-left",
  },
  { source: "generate", target: "check_hallucination", kind: "flow" },
  {
    source: "check_hallucination",
    target: "rewrite_query",
    kind: "feedback-right",
    sourceHandle: "source-right",
    targetHandle: "target-top",
  },
  {
    source: "check_hallucination",
    target: "corroborate",
    kind: "fallback",
    sourceHandle: "source-left",
    targetHandle: "target-right",
  },
  { source: "check_hallucination", target: "format_citations", kind: "flow" },
  {
    source: "corroborate",
    target: "format_citations",
    kind: "flow",
    sourceHandle: "source-right",
    targetHandle: "target-left",
  },
  { source: "plan_research", target: "run_subagents", kind: "flow" },
  { source: "run_subagents", target: "synthesize", kind: "flow" },
  { source: "synthesize", target: "critic", kind: "flow" },
  { source: "direct_answer", target: "end_conversation", kind: "end" },
  { source: "fallback_answer", target: "end_fallback", kind: "end" },
  { source: "format_citations", target: "end_standard", kind: "end" },
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
    const position = POSITIONS[node.id] ?? { x: 8, y: 16, width: NODE_WIDTH };
    const width = position.width ?? NODE_WIDTH;
    return {
      id: node.id,
      type: "ask-agentic",
      position: { x: position.x, y: position.y },
      width,
      height: NODE_HEIGHT,
      data: {
        id: node.id,
        label: FLOW_LABELS[node.id] ?? node.label,
        accessibleLabel: node.label,
        status: node.status,
        activity: node.activity,
        width,
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

  const edges: Edge[] = LINKS.flatMap(({ source, target, kind, sourceHandle, targetHandle }) => {
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
          : kind === "feedback" || kind === "feedback-right"
            ? "ask-agentic-edge-feedback"
            : kind === "end"
              ? "ask-agentic-edge-terminal"
              : "";

    return [
      {
        id: `${source}->${target}`,
        source,
        target,
        type:
          kind === "deep-route"
            ? "deep-route"
            : kind === "feedback-right"
              ? "feedback-right"
              : kind === "feedback"
                ? "feedback"
                : "smoothstep",
        ...(sourceHandle ? { sourceHandle } : {}),
        ...(targetHandle ? { targetHandle } : {}),
        markerEnd: ARROW,
        focusable: false,
        className,
      },
    ];
  });

  return (
    <figure
      className="ycs-ask-flow mx-auto w-full"
      style={{ height: FLOW_HEIGHT }}
      aria-label="YCS Ask LangGraph: prepare and route the question, then run conversation, transcript retrieval and verification, or deep research. Terminal paths end at END."
    >
      <ReactFlow
        nodes={flowNodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.4}
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
