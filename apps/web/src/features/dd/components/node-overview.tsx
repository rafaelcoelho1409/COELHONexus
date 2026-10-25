"use client";

/** Lays out one node's Overview model: headline cards, then its tables, bars and notes, then the footer. */

import { useState } from "react";

import type { NodeOverview, OverviewCell, OverviewSection } from "../domain/overview";

const TONE: Record<NonNullable<OverviewCell["tone"]>, string> = {
  good: "text-success-text",
  bad: "text-destructive-text",
};

function compare(a: string | number | undefined, b: string | number | undefined): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a ?? "").localeCompare(String(b ?? ""));
}

function SectionTable({ section }: { section: Extract<OverviewSection, { kind: "table" }> }) {
  const [sort, setSort] = useState<{ column: number; direction: "ascending" | "descending" } | null>(null);
  // Rows and cells have no natural id; numbering them up front keeps each one's identity through a sort.
  const numbered = section.rows.map((cells, id) => ({
    id,
    cells: cells.map((cell, column) => ({ column, ...cell })),
  }));
  const rows = sort
    ? numbered.slice().sort((a, b) => {
        const left = a.cells[sort.column];
        const right = b.cells[sort.column];
        return (
          compare(left?.sort ?? left?.text, right?.sort ?? right?.text) *
          (sort.direction === "ascending" ? 1 : -1)
        );
      })
    : numbered;
  const toggle = (column: number) =>
    setSort((current) =>
      current?.column === column && current.direction === "ascending"
        ? { column, direction: "descending" }
        : { column, direction: "ascending" },
    );

  return (
    <section
      // biome-ignore lint/a11y/noNoninteractiveTabindex: long tables scroll, so keyboard users must be able to focus the region to scroll it (WCAG 2.1.1)
      tabIndex={0}
      aria-label={section.title}
      className="max-h-80 overflow-auto rounded-sm border border-border"
    >
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {section.columns.map((name, column) => {
              const sortable = section.sortable?.includes(column) ?? false;
              const active = sort?.column === column ? sort.direction : "none";
              return (
                <th
                  key={name}
                  scope="col"
                  aria-sort={sortable ? active : undefined}
                  className="sticky top-0 border-b border-border bg-card px-3 py-2 text-left text-xs font-medium text-muted-foreground"
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => toggle(column)}
                      className="inline-flex min-h-6 items-center gap-1 hover:text-foreground"
                    >
                      {name}
                      <span aria-hidden>
                        {active === "ascending" ? "↑" : active === "descending" ? "↓" : "↕"}
                      </span>
                    </button>
                  ) : (
                    name
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-border last:border-b-0">
              {row.cells.map((cell) => (
                <td
                  key={cell.column}
                  className={`whitespace-pre-line px-3 py-1.5 align-top ${cell.tone ? TONE[cell.tone] : ""}`}
                >
                  {cell.text}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Section({ section }: { section: OverviewSection }) {
  const heading = <h4 className="mb-2 text-xs font-medium text-muted-foreground">{section.title}</h4>;
  switch (section.kind) {
    case "table":
      return (
        <section>
          {heading}
          <SectionTable section={section} />
        </section>
      );
    case "bars": {
      const max = Math.max(1, ...section.rows.map((row) => row.value));
      return (
        <section>
          {heading}
          <ul className="max-h-80 overflow-auto rounded-sm border border-border">
            {section.rows.map((row) => (
              <li
                key={row.id}
                className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 border-b border-border px-3 py-2 last:border-b-0"
              >
                <span className="text-sm">{row.label}</span>
                <span className="text-sm tabular-nums">{row.valueLabel}</span>
                <span aria-hidden className="col-span-2 block h-1.5 rounded-sm bg-muted">
                  <span
                    className="block h-full rounded-sm bg-primary"
                    style={{ width: `${Math.max(2, Math.round((row.value / max) * 100))}%` }}
                  />
                </span>
              </li>
            ))}
          </ul>
        </section>
      );
    }
    case "list":
      return (
        <section>
          {heading}
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            {section.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </section>
      );
    case "figures":
      return (
        <section>
          {heading}
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {section.cells.map((cell) => (
              <div key={cell.key} className="rounded-sm border border-border px-3 py-2">
                <dt className="text-xs text-muted-foreground">{cell.key}</dt>
                <dd className="font-mono text-sm">{cell.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      );
    case "note":
      return (
        <section>
          {heading}
          <p className="rounded-sm border border-border px-3 py-2 text-sm leading-relaxed">{section.text}</p>
        </section>
      );
  }
}

export function NodeOverviewView({ overview }: { overview: NodeOverview }) {
  if (overview.empty) return <p className="text-sm text-muted-foreground">{overview.empty}</p>;
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {overview.cards.map((card) => (
          <div key={card.label} className="rounded-sm border border-border bg-card px-3 py-2">
            <dt className="text-xs text-muted-foreground">{card.label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{card.value}</dd>
            {card.sub ? <dd className="text-xs text-muted-foreground">{card.sub}</dd> : null}
          </div>
        ))}
      </dl>
      {overview.sections.map((section) => (
        <Section key={section.title} section={section} />
      ))}
      {overview.footer.length > 0 ? (
        <p className="font-mono text-xs break-words text-muted-foreground">{overview.footer.join(" · ")}</p>
      ) : null}
    </div>
  );
}
