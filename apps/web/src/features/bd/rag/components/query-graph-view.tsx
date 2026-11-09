"use client";

/** The Graph view of a Neo4j result that holds nodes (the results pane offers it only then): the canvas, a legend of
 * node labels (text beside each colour), and the selected node's properties and relationships. Selecting is by button,
 * never hover, so it works by keyboard and on a phone. The Table view lists every row for anyone who prefers text to
 * a picture. */

import dynamic from "next/dynamic";
import { type JSX, useMemo, useState } from "react";

import { Button } from "@/shared/ui/button";

import type { RawHit } from "../entities";
import { QUERY_GRAPH_MAX_NODES } from "../params";
import { cellText, collectGraph, labelColors, layoutGraph } from "../query";

// The graph library is only needed once a graph is on screen.
const Canvas = dynamic(() => import("./query-graph").then((module) => module.QueryGraphCanvas), {
  ssr: false,
  loading: () => <p className="p-4 text-xs text-muted-foreground">Loading graph…</p>,
});

export function QueryGraphView({ hits }: { hits: ReadonlyArray<RawHit> }): JSX.Element {
  const graph = useMemo(() => collectGraph(hits), [hits]);
  const tooBig = graph.nodes.length > QUERY_GRAPH_MAX_NODES;
  const layout = useMemo(() => (tooBig ? new Map() : layoutGraph(graph)), [graph, tooBig]);
  const colors = useMemo(() => labelColors(graph.nodes), [graph]);
  const [selected, setSelected] = useState<string | null>(null);

  if (tooBig) {
    return (
      <p className="rounded-sm border border-border p-4 text-sm text-muted-foreground">
        This result has {graph.nodes.length.toLocaleString("en-US")} nodes, more than the{" "}
        {QUERY_GRAPH_MAX_NODES} the graph draws. Add a LIMIT, or use the Table view, which lists every row.
      </p>
    );
  }

  const node = graph.nodes.find((candidate) => candidate.id === selected) ?? null;
  const byId = new Map(graph.nodes.map((candidate) => [candidate.id, candidate] as const));
  const touching = node
    ? graph.edges.filter((edge) => edge.source === node.id || edge.target === node.id)
    : [];

  return (
    <div className="flex flex-col gap-3">
      <ul aria-label="Node labels" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        {[...colors.entries()].map(([label, color]) => (
          <li key={label} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="size-3 rounded-sm border border-border"
              style={{ backgroundColor: color }}
            />
            {label}
          </li>
        ))}
        <li className="tabular ml-auto text-xs text-muted-foreground">
          {graph.nodes.length} nodes · {graph.edges.length} relationships
        </li>
      </ul>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <figure
          aria-label="Result graph; select a node to see its properties"
          className="query-graph m-0 h-96 min-w-0 rounded-sm border border-border sm:h-[32rem]"
        >
          <Canvas graph={graph} layout={layout} colors={colors} selected={selected} onSelect={setSelected} />
        </figure>

        <section aria-label="Selected node" className="min-w-0 rounded-sm border border-border p-3">
          {node ? (
            <div className="flex flex-col gap-3">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-muted-foreground">{node.label}</p>
                  <h3 className="text-base font-semibold break-words">{node.display}</h3>
                </div>
                <Button variant="outline" size="lg" onClick={() => setSelected(null)}>
                  Clear
                </Button>
              </div>
              {Object.keys(node.properties).length > 0 ? (
                <dl className="flex flex-col gap-1.5 text-sm">
                  {Object.entries(node.properties).map(([key, value]) => (
                    <div key={key} className="min-w-0">
                      <dt className="text-xs text-muted-foreground">{key}</dt>
                      <dd className="line-clamp-4 break-words">{cellText(value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-sm text-muted-foreground">No properties.</p>
              )}
              {touching.length > 0 ? (
                <div>
                  <h4 className="text-xs text-muted-foreground">Relationships</h4>
                  <ul className="mt-1 flex flex-col gap-1 text-sm">
                    {touching.map((edge) => {
                      const outgoing = edge.source === node.id;
                      const other = byId.get(outgoing ? edge.target : edge.source);
                      return (
                        <li key={edge.id} className="break-words">
                          <span className="font-mono text-xs">
                            {outgoing ? `-[${edge.type}]->` : `<-[${edge.type}]-`}
                          </span>{" "}
                          {other?.display ?? "unknown"}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              Select a node to see its properties and to light up the nodes next to it.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
