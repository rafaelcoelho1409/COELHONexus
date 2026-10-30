"use client";

/** The Neo4j result as a graph, drawn with React Flow: positions come from `layoutGraph` (computed once per result,
 * so nothing moves afterwards), every node is a real button that selects it and lights up its neighbours, and an edge
 * runs card border to card border with its relationship type on it. Loaded lazily (`next/dynamic`) by
 * `query-graph-view.tsx`; the Table view is the text equivalent of everything drawn here. */

import "@xyflow/react/dist/style.css";
import "./query-graph.css";

import {
  BaseEdge,
  Controls,
  type Edge,
  type EdgeProps,
  getStraightPath,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";

import { QUERY_GRAPH_NODE_HEIGHT as HEIGHT, QUERY_GRAPH_NODE_WIDTH as WIDTH } from "../params";
import { clipToNode, type Graph, neighborhood, type Point } from "../query";

type EntityData = {
  label: string;
  display: string;
  color: string;
  selected: boolean;
  faded: boolean;
  onSelect: () => void;
};
type EntityNode = Node<EntityData, "entity">;
type LinkData = { from: Point; to: Point };
type LinkEdge = Edge<LinkData, "link">;

/** A node: a real button carrying its label as text (the colour is only a second cue). */
function EntityCard({ data }: NodeProps<EntityNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <button
        type="button"
        onClick={data.onSelect}
        aria-pressed={data.selected}
        // React Flow switches pointer events off for nodes that cannot be dragged or selected, and children
        // inherit that; the button is the one thing here that must stay clickable.
        style={{ width: WIDTH, height: HEIGHT, pointerEvents: "auto", borderColor: data.color }}
        className={`nodrag nopan flex flex-col justify-center overflow-hidden rounded-md border-2 px-3 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
          data.selected ? "bg-muted" : "bg-card"
        } ${data.faded ? "opacity-30" : ""}`}
      >
        <span className="truncate text-xs text-muted-foreground">{data.label}</span>
        <span className="truncate text-sm">{data.display}</span>
      </button>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </>
  );
}

/** A straight line between the two cards' borders (the handles' own coordinates are not used). */
function LinkLine({ id, data, label, markerEnd }: EdgeProps<LinkEdge>) {
  if (!data) return null;
  const start = clipToNode(data.from, data.to, WIDTH, HEIGHT);
  const end = clipToNode(data.to, data.from, WIDTH, HEIGHT);
  const [path, labelX, labelY] = getStraightPath({
    sourceX: start.x,
    sourceY: start.y,
    targetX: end.x,
    targetY: end.y,
  });
  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={markerEnd}
      label={label}
      labelX={labelX}
      labelY={labelY}
      labelBgPadding={[4, 2]}
    />
  );
}

// Stable identities: React Flow re-registers its types whenever these objects change.
const NODE_TYPES = { entity: EntityCard };
const EDGE_TYPES = { link: LinkLine };
const ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14 } as const;

export function QueryGraphCanvas({
  graph,
  layout,
  colors,
  selected,
  onSelect,
}: {
  graph: Graph;
  layout: ReadonlyMap<string, Point>;
  colors: ReadonlyMap<string, string>;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const lit = selected ? neighborhood(graph, selected) : null;
  const center = (id: string): Point => layout.get(id) ?? { x: 0, y: 0 };

  const nodes: EntityNode[] = graph.nodes.map((node) => ({
    id: node.id,
    type: "entity",
    position: { x: center(node.id).x - WIDTH / 2, y: center(node.id).y - HEIGHT / 2 },
    width: WIDTH,
    height: HEIGHT,
    data: {
      label: node.label,
      display: node.display,
      color: colors.get(node.label) ?? "var(--chart-5)",
      selected: node.id === selected,
      faded: lit !== null && !lit.has(node.id),
      onSelect: () => onSelect(node.id === selected ? null : node.id),
    },
  }));

  const edges: LinkEdge[] = graph.edges
    .filter((edge) => edge.source !== edge.target)
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "link",
      label: edge.type,
      markerEnd: ARROW,
      focusable: false,
      className: selected && edge.source !== selected && edge.target !== selected ? "query-edge-faded" : "",
      data: { from: center(edge.source), to: center(edge.target) },
    }));

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.15 }}
      minZoom={0.1}
      maxZoom={1.5}
      nodesDraggable={false}
      nodesConnectable={false}
      nodesFocusable={false}
      edgesFocusable={false}
      elementsSelectable={false}
      zoomOnScroll={false}
      preventScrolling={false}
      proOptions={{ hideAttribution: false }}
    >
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
