"use client";

/** The result rows as a native table: sortable column headers (`aria-sort`), an optional filter row, a column picker, a
 * row cap with "Show more", CSV / JSON / Copy exports of the filtered and sorted set, and a record sheet that shows one
 * row in full. Long text clamps to a few lines and identifiers are cut in the middle, so a row stays a few lines tall
 * however long a transcript is; the header and the row-number column stay in view while the table scrolls. A static
 * `table` rather than a `grid`: nothing here is edited, so there is one tab stop per control and a screen reader gets
 * the plain table model. */

import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, Filter } from "lucide-react";
import { type JSX, useMemo, useState } from "react";

import { Button } from "@/shared/ui/button";

import { useYcsOverlays } from "../hooks/use-overlays";
import { QUERY_ID_CLIP, QUERY_ROWS_STEP } from "../params";
import {
  type ColumnKind,
  cellKind,
  cellText,
  clip,
  filterRows,
  inferColumns,
  middleClip,
  nestedLabel,
  nextSort,
  planColumns,
  type Row,
  type Sort,
  sortRows,
  toCsv,
  toJsonText,
} from "../query";
import { QueryRecordSheet } from "./query-record-sheet";

function download(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function SortIcon({ dir }: { dir: "asc" | "desc" | null }): JSX.Element {
  if (dir === "asc") return <ArrowUp aria-hidden className="size-4" />;
  if (dir === "desc") return <ArrowDown aria-hidden className="size-4" />;
  return <ArrowUpDown aria-hidden className="size-4 text-muted-foreground" />;
}

/** The width a column asks for: a text column gets room to read, everything else stays narrow. */
const CELL_WIDTH: Record<ColumnKind, string> = {
  text: "min-w-64 max-w-md",
  id: "whitespace-nowrap",
  number: "whitespace-nowrap",
  plain: "min-w-24 max-w-xs",
};

function Cell({
  column,
  kind,
  value,
  onOpen,
}: {
  column: string;
  kind: ColumnKind;
  value: unknown;
  onOpen: () => void;
}): JSX.Element {
  const shape = cellKind(value);
  if (shape === "null") return <span className="text-muted-foreground">null</span>;
  if (shape === "empty") return <span className="text-muted-foreground">&quot;&quot;</span>;
  if (shape === "array" || shape === "object") {
    return (
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open ${column}, ${nestedLabel(value)}`}
        className="min-h-9 rounded-sm border border-border px-2 font-mono text-xs hover:bg-muted"
      >
        {nestedLabel(value)}
      </button>
    );
  }
  const text = cellText(value);
  if (kind === "id") {
    return (
      <span title={text} className="font-mono text-xs">
        {middleClip(text, QUERY_ID_CLIP)}
      </span>
    );
  }
  if (shape === "number") return <span className="tabular">{text}</span>;
  return <div className="line-clamp-3 break-words">{clip(text)}</div>;
}

export function QueryTable({ rows }: { rows: ReadonlyArray<Row> }): JSX.Element {
  const { toast } = useYcsOverlays();
  const plan = useMemo(() => planColumns(rows), [rows]);
  const [sort, setSort] = useState<Sort>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [filtering, setFiltering] = useState(false);
  const [picking, setPicking] = useState(false);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(
    () => new Set(plan.filter((entry) => entry.hidden).map((entry) => entry.column)),
  );
  const [shown, setShown] = useState(QUERY_ROWS_STEP);
  const [openRow, setOpenRow] = useState<number | null>(null);

  const exportColumns = inferColumns(rows);
  const columns = plan.filter((entry) => !hidden.has(entry.column));
  const visible = sortRows(filterRows(rows, filters), sort);
  // The row's place in the current order is its identity (result rows carry no id of their own).
  const painted = visible.slice(0, shown).map((row, position) => ({ row, position }));

  const copyAll = () => {
    navigator.clipboard.writeText(toCsv(exportColumns, visible)).then(
      () => toast(`Copied ${visible.length} rows as CSV`),
      () => toast("Copy was blocked by the browser"),
    );
  };

  const toggleColumn = (column: string, on: boolean) =>
    setHidden((current) => {
      const next = new Set(current);
      if (on) next.delete(column);
      else next.add(column);
      return next;
    });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <p role="status" className="tabular mr-auto text-sm text-muted-foreground">
          {visible.length === rows.length
            ? `${rows.length.toLocaleString("en-US")} rows`
            : `${visible.length.toLocaleString("en-US")} of ${rows.length.toLocaleString("en-US")} rows match`}
        </p>
        <Button variant="outline" size="lg" aria-pressed={picking} onClick={() => setPicking((was) => !was)}>
          <Columns3 aria-hidden /> Columns
        </Button>
        <Button
          variant="outline"
          size="lg"
          aria-pressed={filtering}
          onClick={() => setFiltering((was) => !was)}
        >
          <Filter aria-hidden /> Filter columns
        </Button>
        <Button
          variant="outline"
          size="lg"
          onClick={() => download("results.csv", toCsv(exportColumns, visible), "text/csv")}
        >
          Export CSV
        </Button>
        <Button
          variant="outline"
          size="lg"
          onClick={() => download("results.json", toJsonText(visible), "application/json")}
        >
          Export JSON
        </Button>
        <Button variant="outline" size="lg" onClick={copyAll}>
          Copy CSV
        </Button>
      </div>

      {picking ? (
        <fieldset className="m-0 flex flex-wrap gap-x-4 gap-y-0 rounded-sm border border-border px-3 pb-1">
          <legend className="px-1 text-xs text-muted-foreground">Show columns (exports keep them all)</legend>
          {plan.map((entry) => (
            <label key={entry.column} className="flex min-h-9 cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!hidden.has(entry.column)}
                onChange={(event) => toggleColumn(entry.column, event.target.checked)}
                className="size-4 cursor-pointer accent-primary"
              />
              <span className="break-all">{entry.column}</span>
            </label>
          ))}
        </fieldset>
      ) : null}

      <section
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
        tabIndex={0}
        aria-label="Result rows"
        // `relative` makes the scroller the containing block of the screen-reader-only text inside its cells; without
        // it that text is positioned against the page and widens it.
        className="relative max-h-[32rem] overflow-auto rounded-sm border border-border"
      >
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Query results; column headers sort the rows.</caption>
          <thead className="sticky top-0 z-10 bg-card">
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th scope="col" className="sticky left-0 z-20 bg-card px-3 py-2 text-right font-medium">
                #
              </th>
              {columns.map(({ column }) => {
                const dir = sort?.column === column ? sort.dir : null;
                return (
                  <th
                    key={column}
                    scope="col"
                    aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}
                    className="font-medium"
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setSort((current) => nextSort(current, column));
                        setShown(QUERY_ROWS_STEP);
                      }}
                      className="flex min-h-9 w-full items-center gap-1 px-3 text-left whitespace-nowrap hover:text-foreground"
                    >
                      <span>{column}</span>
                      <SortIcon dir={dir} />
                    </button>
                  </th>
                );
              })}
            </tr>
            {filtering ? (
              <tr className="border-b border-border">
                <td className="sticky left-0 z-20 bg-card px-3" />
                {columns.map(({ column }) => (
                  <td key={column} className="px-2 py-1">
                    <input
                      type="search"
                      value={filters[column] ?? ""}
                      onChange={(event) => {
                        setFilters((current) => ({ ...current, [column]: event.target.value }));
                        setShown(QUERY_ROWS_STEP);
                      }}
                      aria-label={`Filter ${column}`}
                      placeholder="Filter"
                      className="min-h-9 w-full min-w-24 rounded-sm border border-input bg-background px-2 text-sm"
                    />
                  </td>
                ))}
              </tr>
            ) : null}
          </thead>
          <tbody>
            {painted.map(({ row, position }) => (
              <tr key={position} className="border-b border-border align-top last:border-b-0">
                <th scope="row" className="sticky left-0 z-1 bg-card px-1 py-0.5 text-right font-normal">
                  <button
                    type="button"
                    onClick={() => setOpenRow(position)}
                    aria-label={`Open row ${position + 1}`}
                    className="tabular inline-flex min-h-9 min-w-9 items-center justify-center rounded-sm px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    {position + 1}
                  </button>
                </th>
                {columns.map(({ column, kind }) => (
                  <td key={column} className={`px-3 py-2 ${CELL_WIDTH[kind]}`}>
                    <Cell
                      column={column}
                      kind={kind}
                      value={row[column]}
                      onOpen={() => setOpenRow(position)}
                    />
                  </td>
                ))}
              </tr>
            ))}
            {painted.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + 1}
                  className="px-3 py-6 text-center text-sm text-muted-foreground"
                >
                  No row matches the filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      {visible.length > shown ? (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => setShown((count) => count + QUERY_ROWS_STEP)}>
            Show {Math.min(QUERY_ROWS_STEP, visible.length - shown)} more ({visible.length - shown} left)
          </Button>
        </div>
      ) : null}

      {openRow !== null ? (
        <QueryRecordSheet
          columns={plan.map((entry) => entry.column)}
          rows={visible}
          index={openRow}
          onIndex={setOpenRow}
          onClose={() => setOpenRow(null)}
        />
      ) : null}
    </div>
  );
}
