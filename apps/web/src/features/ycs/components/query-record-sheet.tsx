"use client";

/** One result row, read in full: every field with its whole value (the table clamps long text and cuts identifiers),
 * as a field list or as JSON, with Previous and Next to walk the rows. Opens from a row's number or a nested-value
 * pill. The same sheet serves a search hit, a stored chunk and a graph row. */

import { ChevronLeft, ChevronRight } from "lucide-react";
import { type JSX, useState } from "react";

import { Button } from "@/shared/ui/button";

import { useYcsOverlays } from "../hooks/use-overlays";
import { QUERY_TREE_OPEN_DEPTH } from "../params";
import { cellKind, cellText, type Row, toJsonText } from "../query";
import { JsonTree } from "./json-tree";
import { SideSheet } from "./side-sheet";

type View = "fields" | "json";

function FieldValue({ column, value }: { column: string; value: unknown }): JSX.Element {
  const kind = cellKind(value);
  if (kind === "null") return <span className="text-muted-foreground">null</span>;
  if (kind === "empty") return <span className="text-muted-foreground">&quot;&quot;</span>;
  if (kind === "array" || kind === "object") {
    return <JsonTree value={value} label={`${column} value`} openDepth={1} />;
  }
  return <p className="text-sm break-words whitespace-pre-wrap">{cellText(value)}</p>;
}

export function QueryRecordSheet({
  columns,
  rows,
  index,
  onIndex,
  onClose,
}: {
  /** Every column, hidden ones included: the sheet is where the table's omissions can be read. */
  columns: ReadonlyArray<string>;
  rows: ReadonlyArray<Row>;
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
}): JSX.Element | null {
  const { toast } = useYcsOverlays();
  const [view, setView] = useState<View>("fields");
  const row = rows[index];
  if (!row) return null;

  const copyJson = () => {
    navigator.clipboard.writeText(toJsonText([row])).then(
      () => toast("Copied the row as JSON"),
      () => toast("Copy was blocked by the browser"),
    );
  };

  return (
    <SideSheet title="Row" closeLabel="Close row" onClose={onClose}>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="lg" disabled={index === 0} onClick={() => onIndex(index - 1)}>
            <ChevronLeft aria-hidden /> Previous
          </Button>
          <Button
            variant="outline"
            size="lg"
            disabled={index >= rows.length - 1}
            onClick={() => onIndex(index + 1)}
          >
            Next <ChevronRight aria-hidden />
          </Button>
          <p role="status" className="tabular text-sm text-muted-foreground">
            Row {index + 1} of {rows.length.toLocaleString("en-US")}
          </p>
          <fieldset className="m-0 ml-auto flex min-w-0 gap-1 border-0 p-0">
            <legend className="sr-only">Row view</legend>
            {(["fields", "json"] as const).map((id) => (
              <Button
                key={id}
                variant={view === id ? "secondary" : "outline"}
                size="lg"
                aria-pressed={view === id}
                onClick={() => setView(id)}
              >
                {id === "fields" ? "Fields" : "JSON"}
              </Button>
            ))}
          </fieldset>
        </div>

        {view === "fields" ? (
          <dl className="divide-y divide-border rounded-sm border border-border">
            {columns.map((column) => (
              <div key={column} className="px-3 py-2">
                <dt className="font-mono text-xs break-all text-muted-foreground">{column}</dt>
                <dd className="mt-1 min-w-0">
                  <FieldValue column={column} value={row[column]} />
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <div className="flex flex-col items-start gap-2">
            <Button variant="outline" size="lg" onClick={copyJson}>
              Copy JSON
            </Button>
            <div className="w-full min-w-0 rounded-sm border border-border p-2">
              <JsonTree value={row} label="Row JSON" openDepth={QUERY_TREE_OPEN_DEPTH} />
            </div>
          </div>
        )}
      </div>
    </SideSheet>
  );
}
