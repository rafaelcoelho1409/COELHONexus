"use client";

/** Fixed, read-only workflow graph. Both store nodes branch from Elasticsearch. */

import "@xyflow/react/dist/style.css";
import "./ingestion-agentic-flow.css";

import { type Edge, Handle, MarkerType, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";

import { STATUS_WORD_LABEL, type StatusWord, statusWord } from "../domain";
import { type Step, StepProgress } from "./pipeline-steps";
import { StatusBadge } from "./status-badge";

const NODE_WIDTH = 168;
const NODE_HEIGHT = 96;
const FLOW_WIDTH = 368;
const FLOW_HEIGHT = 388;
const POSITIONS: Record<string, { x: number; y: number }> = {
  playwright: { x: 100, y: 8 },
  elasticsearch: { x: 100, y: 132 },
  qdrant: { x: 8, y: 260 },
  neo4j: { x: 192, y: 260 },
};

type IngestionFlowNodeData = {
  label: string;
  status: StatusWord;
  detail: string;
  step: Step;
  onOpen: () => void;
};
type IngestionFlowNode = Node<IngestionFlowNodeData, "ingestion">;

function IngestionNodeCard({ data }: NodeProps<IngestionFlowNode>) {
  const status = STATUS_WORD_LABEL[data.status];
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <button
        type="button"
        onClick={data.onOpen}
        aria-haspopup="dialog"
        data-status={data.status}
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT, pointerEvents: "auto" }}
        className={`nodrag nopan flex flex-col justify-between overflow-hidden rounded-md border bg-card px-3 py-2 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
          data.status === "running"
            ? "border-running"
            : data.status === "failed"
              ? "border-destructive"
              : data.status === "partial"
                ? "border-warning"
                : "border-border"
        }`}
      >
        <span className="flex min-w-0 items-center justify-between gap-1.5">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="min-w-0 truncate text-sm font-medium">{data.label}</span>
          </span>
          <StatusBadge tone={data.status} label={status} className="shrink-0" />
        </span>
        <span className="block truncate font-mono text-xs text-muted-foreground">{data.step.key}</span>
        <span title={data.detail} className="block truncate font-mono text-xs text-muted-foreground">
          {data.detail}
        </span>
        <StepProgress step={data.step} />
      </button>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </>
  );
}

const NODE_TYPES = { ingestion: IngestionNodeCard };
const ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14 } as const;

export function IngestionAgenticFlow({
  steps,
  onOpen,
}: {
  steps: ReadonlyArray<Step>;
  onOpen: (key: string) => void;
}) {
  const stepByKey = new Map(steps.map((step) => [step.key, step]));
  const nodes: IngestionFlowNode[] = steps.map((step) => {
    const key = step.key;
    const label = step.title.replace(/^Phase \d+ · /, "");
    return {
      id: key,
      type: "ingestion",
      position: POSITIONS[key] ?? { x: 100, y: 8 },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        label,
        status: statusWord(step.view.state),
        detail: step.view.label,
        step,
        onOpen: () => onOpen(key),
      },
    };
  });
  const links = [
    ["playwright", "elasticsearch"],
    ["elasticsearch", "qdrant"],
    ["elasticsearch", "neo4j"],
  ] as const;
  const edges: Edge[] = links.flatMap(([source, target]) => {
    const sourceStep = stepByKey.get(source);
    const targetStep = stepByKey.get(target);
    if (!sourceStep || !targetStep) return [];
    const active =
      statusWord(sourceStep.view.state) === "done" && statusWord(targetStep.view.state) === "running";
    return [
      {
        id: `${source}->${target}`,
        source,
        target,
        markerEnd: ARROW,
        focusable: false,
        className: active ? "ingestion-agentic-edge-active" : "",
      },
    ];
  });

  return (
    <figure
      className="ycs-ingestion-flow mx-auto max-w-full"
      style={{ width: FLOW_WIDTH, height: FLOW_HEIGHT }}
      data-testid="ycs-ingestion-flow"
      aria-label="Playwright transcript retrieval followed by Elasticsearch indexing, then Qdrant and Neo4j writes"
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
