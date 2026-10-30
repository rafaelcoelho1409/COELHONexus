"use client";

/** The results pane: an empty state that says how to start, a running note, the error band (a rejected or failed
 * query), then the stats, the server's notes and a Table | JSON (| Graph, for Neo4j) switch over one result. */

import { type JSX, useMemo, useState } from "react";

import { Button } from "@/shared/ui/button";

import { QUERY_TREE_OPEN_DEPTH, type QueryBackend } from "../params";
import { collectGraph, flattenRows, type RunState } from "../query";
import { JsonTree } from "./json-tree";
import { QueryGraphView } from "./query-graph-view";
import { QueryTable } from "./query-table";

type View = "table" | "json" | "graph";

const VIEW_LABEL: Record<View, string> = { table: "Table", json: "JSON", graph: "Graph" };

/** A result with more rows than this starts its JSON tree folded, so the page opens on a list, not a wall. */
const JSON_FOLD_ABOVE = 10;

function Notes({ notes }: { notes: ReadonlyArray<string> }): JSX.Element | null {
  if (notes.length === 0) return null;
  return (
    <div role="status" className="rounded-sm border border-border px-3 py-2 text-sm">
      <p className="text-xs text-muted-foreground">Notes</p>
      <ul className="mt-1 list-disc pl-5">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </div>
  );
}

export function QueryResults({ backend, state }: { backend: QueryBackend; state: RunState }): JSX.Element {
  // Until the reader picks one, the view follows the result: whole nodes and relationships open as a graph, plain
  // values as a table. A new result remounts this component, so each Run starts from the result's own shape again.
  const [picked, setPicked] = useState<View | null>(null);
  const [openDepth, setOpenDepth] = useState<number | null>(null);
  const hits = state.kind === "done" ? state.result.hits : null;
  const rows = useMemo(() => (hits ? flattenRows(backend, hits) : []), [backend, hits]);
  const drawable = useMemo(
    () => backend === "neo4j" && hits !== null && collectGraph(hits).nodes.length > 0,
    [backend, hits],
  );

  if (state.kind === "idle") {
    return (
      <div className="rounded-sm border border-border p-6">
        <h2 className="text-base font-semibold">Query workbench</h2>
        <p className="mt-2 max-w-prose text-sm text-muted-foreground">
          Write the query in the editor and press Run, or describe what you need to the assistant and let it
          fill the editor. Pick the store with the tabs above; every query is read-only.
        </p>
      </div>
    );
  }
  if (state.kind === "running") {
    return (
      <p className="rounded-sm border border-border p-6 text-sm text-muted-foreground">Running the query…</p>
    );
  }
  if (state.kind === "failed") {
    return (
      <div role="alert" className="flex flex-col gap-2 rounded-sm border border-destructive px-3 py-3">
        <p className="text-sm font-semibold text-destructive-text">Query failed</p>
        <pre className="font-mono text-sm break-words whitespace-pre-wrap">{state.message}</pre>
        <Notes notes={state.notes} />
      </div>
    );
  }

  const { result } = state;
  // A result with no node or relationship has nothing to draw, so it offers no Graph view.
  const views: View[] = drawable ? ["table", "json", "graph"] : ["table", "json"];
  const current: View = picked && views.includes(picked) ? picked : drawable ? "graph" : "table";
  const total = result.total ?? result.hits.length;
  const depth = openDepth ?? (result.hits.length > JSON_FOLD_ABOVE ? 0 : QUERY_TREE_OPEN_DEPTH);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">Results</h2>
        <p className="tabular text-sm text-muted-foreground">
          {result.hits.length.toLocaleString("en-US")} of {total.toLocaleString("en-US")} rows ·{" "}
          {result.took_ms} ms
        </p>
        <fieldset className="m-0 ml-auto flex min-w-0 gap-1 border-0 p-0">
          <legend className="sr-only">Result view</legend>
          {views.map((id) => (
            <Button
              key={id}
              variant={current === id ? "secondary" : "outline"}
              size="lg"
              aria-pressed={current === id}
              onClick={() => setPicked(id)}
            >
              {VIEW_LABEL[id]}
            </Button>
          ))}
        </fieldset>
      </div>
      <Notes notes={result.notes} />
      {result.hits.length === 0 ? (
        <p className="rounded-sm border border-border p-6 text-sm text-muted-foreground">
          The query ran and returned no rows.
        </p>
      ) : current === "table" ? (
        <QueryTable rows={rows} />
      ) : current === "json" ? (
        <div className="flex flex-col gap-2">
          <div className="flex gap-1">
            <Button variant="outline" size="lg" onClick={() => setOpenDepth(Number.POSITIVE_INFINITY)}>
              Expand all
            </Button>
            <Button variant="outline" size="lg" onClick={() => setOpenDepth(0)}>
              Collapse all
            </Button>
          </div>
          <section
            // biome-ignore lint/a11y/noNoninteractiveTabindex: a long tree scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
            tabIndex={0}
            aria-label="Result JSON tree"
            className="relative max-h-[32rem] overflow-auto rounded-sm border border-border p-2"
          >
            <JsonTree
              key={depth}
              value={result.hits.map((hit) => hit.raw)}
              label="Result JSON"
              openDepth={depth}
            />
          </section>
        </div>
      ) : (
        <QueryGraphView hits={result.hits} />
      )}
    </div>
  );
}
