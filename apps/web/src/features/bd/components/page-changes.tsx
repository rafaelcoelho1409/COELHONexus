"use client";

/** What the Agentic AI step changed on this page: the lines it took out and put in, with the words that differ marked, and
 * a little unchanged text around each change. The sign in front of a line says what happened to it, so the meaning never
 * rests on colour alone. */

import type { DiffRow, DiffToken, PageDiff } from "../domain/page-diff";

function Tokens({ tokens, tone }: { tokens: DiffToken[]; tone: "removed" | "added" }) {
  return (
    <>
      {tokens.map((token) =>
        token.changed ? (
          <mark
            key={token.id}
            className={`bg-transparent font-semibold text-inherit decoration-2 ${
              tone === "removed"
                ? "line-through decoration-destructive"
                : "underline underline-offset-2 decoration-success"
            }`}
          >
            {token.text}
          </mark>
        ) : (
          <span key={token.id}>{token.text}</span>
        ),
      )}
    </>
  );
}

function Row({ row }: { row: DiffRow }) {
  if (row.kind === "skip") {
    return (
      <li className="px-3 py-1 text-xs text-muted-foreground">
        … {row.count} unchanged {row.count === 1 ? "line" : "lines"}
      </li>
    );
  }
  const sign = row.kind === "removed" ? "−" : row.kind === "added" ? "+" : " ";
  const label = row.kind === "removed" ? "Removed: " : row.kind === "added" ? "Added: " : "";
  const tone =
    row.kind === "removed"
      ? "bg-destructive/10"
      : row.kind === "added"
        ? "bg-success/10"
        : "text-muted-foreground";
  return (
    <li className={`flex gap-2 px-3 py-0.5 ${tone}`} data-kind={row.kind}>
      <span aria-hidden="true" className="w-3 shrink-0 select-none">
        {sign}
      </span>
      <span className="min-w-0 break-words whitespace-pre-wrap">
        <span className="sr-only">{label}</span>
        {row.kind === "same" ? (
          row.text || " "
        ) : row.text === "" ? (
          <span className="italic">(blank line)</span>
        ) : (
          <Tokens tokens={row.tokens} tone={row.kind} />
        )}
      </span>
    </li>
  );
}

export function PageChanges({
  diff,
  loading,
  failed,
}: {
  diff: PageDiff | null;
  loading: boolean;
  failed: boolean;
}) {
  if (failed) {
    return (
      <p role="alert" className="text-sm text-destructive-text">
        The raw Docling page could not be loaded to compare.
      </p>
    );
  }
  if (loading || diff === null) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Comparing with the raw extraction…
      </p>
    );
  }
  if (diff.identical) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        No changes: the Agentic AI step left this page exactly as Docling extracted it.
      </p>
    );
  }
  return (
    <section aria-label="Changes made by Agentic AI">
      <p role="status" className="mb-2 text-xs text-muted-foreground">
        {diff.changedLines} changed {diff.changedLines === 1 ? "line" : "lines"} · Docling extraction →
        AI-processed
      </p>
      <ul className="rounded-sm border border-border py-1 font-mono text-xs">
        {diff.rows.map((row) => (
          <Row key={row.id} row={row} />
        ))}
      </ul>
    </section>
  );
}
