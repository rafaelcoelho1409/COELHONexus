"use client";

/** Fixed, read-only workflow graph. Both store nodes branch from Elasticsearch. */

import "@xyflow/react/dist/style.css";
import "./ingestion-agentic-flow.css";

import { Play } from "lucide-react";
import { type Edge, Handle, MarkerType, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";

import { STATUS_WORD_LABEL, type StatusWord, statusWord } from "../domain";
import { type Step, StepProgress } from "./pipeline-steps";
import { StatusBadge } from "./status-badge";

const NODE_WIDTH = 168;
const NODE_HEIGHT = 136;
const FLOW_WIDTH = 368;
const FLOW_HEIGHT = 488;
const POSITIONS: Record<string, { x: number; y: number }> = {
  playwright: { x: 100, y: 8 },
  elasticsearch: { x: 100, y: 164 },
  qdrant: { x: 8, y: 320 },
  neo4j: { x: 192, y: 320 },
};

type IngestionFlowNodeData = {
  label: string;
  status: StatusWord;
  detail: string;
  step: Step;
  onOpen: () => void;
  onPlay?: () => void;
};
type IngestionFlowNode = Node<IngestionFlowNodeData, "ingestion">;

function IngestionNodeCard({ data }: NodeProps<IngestionFlowNode>) {
  const status = STATUS_WORD_LABEL[data.status];
  const detailsId = `ycs-ingestion-node-${data.step.key}-details`;
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <fieldset
        aria-label={`${data.label} — ${status}`}
        data-status={data.status}
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT, pointerEvents: "none" }}
        className={`relative isolate m-0 flex min-w-0 flex-col overflow-hidden rounded-md border bg-card p-0 hover:bg-muted ${
          data.status === "running"
            ? "border-running"
            : data.status === "failed"
              ? "border-destructive"
              : data.status === "partial"
                ? "border-warning"
                : "border-border"
        }`}
      >
        <legend className="sr-only">{`${data.label} — ${status}`}</legend>
        <button
          type="button"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            data.onPlay?.();
          }}
          aria-label={`Replay from ${data.label}`}
          title={`Replay from ${data.label} and clear downstream results`}
          style={{ pointerEvents: "auto", zIndex: 50 }}
          className="nodrag nopan absolute left-1 top-1 inline-flex size-9 items-center justify-center rounded-sm bg-card text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          hidden={!data.onPlay}
        >
          <Play aria-hidden className="size-4" />
        </button>
        <button
          type="button"
          onClick={data.onOpen}
          aria-label={`Open ${data.label} node details`}
          aria-describedby={detailsId}
          aria-haspopup="dialog"
          style={{
            pointerEvents: "auto",
            clipPath: data.onPlay
              ? "polygon(44px 0, 100% 0, 100% 100%, 0 100%, 0 44px, 44px 44px)"
              : undefined,
          }}
          className="nodrag nopan absolute inset-0 z-10 flex min-h-0 flex-col overflow-hidden rounded-md border-0 bg-transparent p-0 text-left text-foreground hover:bg-muted/20 focus-visible:outline-2 focus-visible:outline-ring"
        >
          <div id={detailsId} className="pointer-events-none flex min-h-0 flex-1 flex-col">
            <span className={`flex min-h-10 flex-col pt-2 ${data.onPlay ? "pl-11" : "px-3"}`}>
              <span className="truncate text-sm leading-4">{data.label}</span>
              <span className="flex min-w-0 items-center justify-between gap-2">
                <span className="truncate font-mono text-xs text-muted-foreground">{data.step.key}</span>
                <StatusBadge tone={data.status} label={status} className="shrink-0" />
              </span>
            </span>
            <span
              title={data.detail}
              className="pointer-events-none block truncate px-3 pt-1 font-mono text-xs text-muted-foreground"
            >
              {data.detail}
            </span>
            <span className="pointer-events-none mt-auto block px-3 pb-2 pt-1">
              <StepProgress step={data.step} />
            </span>
          </div>
        </button>
      </fieldset>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </>
  );
}

const NODE_TYPES = { ingestion: IngestionNodeCard };
const ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14 } as const;

export function IngestionAgenticFlow({
  steps,
  onOpen,
  onPlay,
}: {
  steps: ReadonlyArray<Step>;
  onOpen: (key: string) => void;
  onPlay?: (key: string) => void;
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
        ...(onPlay ? { onPlay: () => onPlay(key) } : {}),
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
