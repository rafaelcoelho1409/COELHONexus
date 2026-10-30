"use client";

/** A read-only JSON tree: each object or array is a disclosure button (`aria-expanded`), so it works with the keyboard
 * and a screen reader without a tree widget's arrow-key model. Long arrays and objects show their first entries and
 * reveal the rest on request. */

import { ChevronRight } from "lucide-react";
import { type JSX, useState } from "react";

import { cellKind, nestedLabel } from "../query";

/** Children painted per branch before "Show more". */
const CHILD_STEP = 100;

function entriesOf(value: unknown): Array<[string, unknown]> {
  if (Array.isArray(value)) return value.map((item, i) => [String(i), item]);
  return Object.entries(value as Record<string, unknown>);
}

function Scalar({ value }: { value: unknown }): JSX.Element {
  const kind = cellKind(value);
  if (kind === "null") return <span className="text-running-text">null</span>;
  if (kind === "empty") return <span className="text-success-text">&quot;&quot;</span>;
  if (kind === "number") return <span className="tabular text-warning-text">{String(value)}</span>;
  if (kind === "boolean") return <span className="text-running-text">{String(value)}</span>;
  return (
    <span className="break-words whitespace-pre-wrap text-success-text">&quot;{String(value)}&quot;</span>
  );
}

function Entry({
  name,
  value,
  depth,
  openDepth,
}: {
  name: string;
  value: unknown;
  depth: number;
  openDepth: number;
}): JSX.Element {
  const kind = cellKind(value);
  const nested = kind === "array" || kind === "object";
  const [open, setOpen] = useState(depth < openDepth);
  const [shown, setShown] = useState(CHILD_STEP);

  if (!nested) {
    return (
      <li className="flex min-w-0 gap-2 py-0.5 pl-6 font-mono text-sm">
        <span className="shrink-0 text-muted-foreground">{name}:</span>
        <Scalar value={value} />
      </li>
    );
  }
  const entries = entriesOf(value);
  return (
    <li className="font-mono text-sm">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className="flex min-h-9 w-full items-center gap-1 rounded-sm text-left hover:bg-muted"
      >
        <ChevronRight aria-hidden className={`size-4 shrink-0 ${open ? "rotate-90" : ""}`} />
        <span className="break-all">{name}</span>
        <span className="text-muted-foreground">{nestedLabel(value)}</span>
      </button>
      {open ? (
        <ul className="ml-2 border-l border-border pl-2">
          {entries.slice(0, shown).map(([key, item]) => (
            <Entry key={key} name={key} value={item} depth={depth + 1} openDepth={openDepth} />
          ))}
          {entries.length > shown ? (
            <li className="py-1 pl-6">
              <button
                type="button"
                onClick={() => setShown((count) => count + CHILD_STEP)}
                className="min-h-9 rounded-sm px-2 text-xs text-primary-text hover:bg-muted"
              >
                Show {Math.min(CHILD_STEP, entries.length - shown)} more of {entries.length - shown}
              </button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}

/** `openDepth` is how many levels start open (0 collapses everything); change it together with `key` to re-fold. */
export function JsonTree({
  value,
  label,
  openDepth,
}: {
  value: unknown;
  label: string;
  openDepth: number;
}): JSX.Element {
  const kind = cellKind(value);
  if (kind !== "array" && kind !== "object") {
    return (
      <p className="font-mono text-sm">
        <span className="sr-only">{label}: </span>
        <Scalar value={value} />
      </p>
    );
  }
  return (
    <ul aria-label={label} className="min-w-0">
      {entriesOf(value).map(([key, item]) => (
        <Entry key={key} name={key} value={item} depth={0} openDepth={openDepth} />
      ))}
    </ul>
  );
}
