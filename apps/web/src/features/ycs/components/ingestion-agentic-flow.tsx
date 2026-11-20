"use client";

/** Fixed, read-only workflow graph. Both store nodes branch from Elasticsearch. */

import "@xyflow/react/dist/style.css";
import "./ingestion-agentic-flow.css";

import {
  Controls,
  type Edge,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { LoaderCircle, Play } from "lucide-react";

import { ReactFlowNodeInternals } from "@/shared/lib/react-flow-node-internals";

import { STATUS_WORD_LABEL, type StatusWord, statusWord } from "../domain";
import { IngestionStatusGlyph } from "./ingestion-status-glyph";
import { type Step, StepProgress } from "./pipeline-steps";

const NODE_WIDTH = 168;
const NODE_HEIGHT = 140;
const POSITIONS: Record<string, { x: number; y: number }> = {
  playwright: { x: 100, y: 8 },
  elasticsearch: { x: 100, y: 168 },
  qdrant: { x: 8, y: 328 },
  neo4j: { x: 196, y: 328 },
};

type IngestionFlowNodeData = {
  label: string;
  status: StatusWord;
  detail: string;
  step: Step;
  onOpen: () => void;
  onPlay?: () => void;
  playDisabled: boolean;
  playPending: boolean;
};
type IngestionFlowNode = Node<IngestionFlowNodeData, "ingestion">;

function IngestionNodeCard({ data }: NodeProps<IngestionFlowNode>) {
  const status = STATUS_WORD_LABEL[data.status];
  const borderTone =
    data.status === "running"
      ? "border-running"
      : data.status === "failed"
        ? "border-destructive"
        : data.status === "partial"
          ? "border-warning"
          : "border-border";
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div
        data-node-id={data.step.key}
        data-status={data.status}
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
        className={`flex h-full w-full cursor-pointer flex-col overflow-hidden rounded-md border bg-card ${borderTone}`}
      >
        <header className="flex min-h-9 items-center gap-1 border-b border-border/60 px-1.5">
          <button
            type="button"
            onClick={data.onOpen}
            aria-label={`Open ${data.label} node details — ${status}`}
            aria-haspopup="dialog"
            className="nodrag nopan flex min-w-0 flex-1 items-center gap-1.5 rounded-sm px-1 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
          >
            <IngestionStatusGlyph status={data.status} />
            <span className="min-w-0 truncate text-sm">{data.label}</span>
            <span className="sr-only">— {status}</span>
          </button>
          {data.onPlay ? (
            <button
              type="button"
              onPointerDown={(event) => event.stopPropagation()}
              onMouseDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                data.onPlay?.();
              }}
              disabled={data.playDisabled || data.playPending}
              aria-busy={data.playPending}
              aria-label={
                data.playPending ? `Queueing replay from ${data.label}` : `Replay from ${data.label}`
              }
              title={`Replay from ${data.label} and clear downstream results`}
              className="nodrag nopan inline-flex size-8 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-60"
            >
              {data.playPending ? (
                <LoaderCircle aria-hidden className="size-4 animate-spin" />
              ) : (
                <Play aria-hidden className="size-4" />
              )}
            </button>
          ) : null}
        </header>
        <button
          type="button"
          onClick={data.onOpen}
          aria-label={`Open ${data.label} node progress details`}
          aria-haspopup="dialog"
          className="nodrag nopan flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden px-3 py-2 text-left hover:bg-muted focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-ring"
        >
          <span className="truncate font-mono text-xs text-muted-foreground">{data.step.key}</span>
          <span title={data.detail} className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {data.detail}
          </span>
          <span
            title={data.step.view.hint ?? data.step.defaultHint}
            className="mt-1 line-clamp-2 text-xs text-muted-foreground"
          >
            {data.step.view.hint ?? data.step.defaultHint}
          </span>
          <span className="mt-auto block pt-1">
            <StepProgress step={data.step} />
          </span>
        </button>
      </div>
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
  replayDisabled = false,
  replayPending = false,
  replayPendingNodeId = null,
}: {
  steps: ReadonlyArray<Step>;
  onOpen: (key: string) => void;
  onPlay?: (key: string) => void;
  replayDisabled?: boolean;
  replayPending?: boolean;
  replayPendingNodeId?: string | null;
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
        playDisabled: replayDisabled || replayPending,
        playPending: replayPending && replayPendingNodeId === key,
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
      className="ycs-ingestion-flow mx-auto h-[min(76vh,680px)] min-h-[520px] w-full min-w-0"
      data-testid="ycs-ingestion-flow"
      aria-label="Playwright transcript retrieval followed by Elasticsearch indexing, then Qdrant and Neo4j writes"
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={(_event, node) => onOpen(node.id)}
        defaultViewport={{ x: 0, y: 0, zoom: 0.82 }}
        fitView
        fitViewOptions={{ padding: 0.06 }}
        minZoom={0.25}
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
        <ReactFlowNodeInternals nodeIds={nodes.map((node) => node.id)} revision={nodes} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </figure>
  );
}
