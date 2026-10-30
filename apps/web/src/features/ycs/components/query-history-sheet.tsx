"use client";

/** Saved queries for the current backend (every successful Run is recorded, a repeat of the same query once): restore
 * one into the editor, or delete it. The same query run several times shows as one entry with its run count. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

import { deleteQueryHistory, fetchQueryHistory } from "../api";
import { useYcsOverlays } from "../hooks/use-overlays";
import { QUERY_BACKENDS, type QueryBackend } from "../params";
import { groupHistory, whenLabel } from "../query";
import { SideSheet } from "./side-sheet";

/** The cache key — a successful Run invalidates it so the sheet is current the next time it opens. */
export function queryHistoryKey(backend: QueryBackend): readonly string[] {
  return ["ycs", "query", "history", backend];
}

export function QueryHistorySheet({
  backend,
  onRestore,
  onDeleted,
  onClose,
}: {
  backend: QueryBackend;
  onRestore: (body: string) => void;
  /** After an entry is deleted, so the stage can save that query again if it is run next. */
  onDeleted: () => void;
  onClose: () => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const { toast } = useYcsOverlays();
  const history = useQuery({ queryKey: queryHistoryKey(backend), queryFn: () => fetchQueryHistory(backend) });
  const label = QUERY_BACKENDS.find((entry) => entry.id === backend)?.label ?? backend;
  const entries = history.data ? groupHistory(history.data) : [];

  const remove = async (ids: ReadonlyArray<number>) => {
    const results = await Promise.all(ids.map((id) => deleteQueryHistory(id)));
    await queryClient.invalidateQueries({ queryKey: queryHistoryKey(backend) });
    if (results.every(Boolean)) onDeleted();
    else toast("Could not delete that query.");
  };

  return (
    <SideSheet
      title="Query history"
      subtitle={`${label}, newest first`}
      closeLabel="Close history"
      onClose={onClose}
    >
      {history.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading history…
        </p>
      ) : history.isError ? (
        <div role="alert" className="flex flex-col items-start gap-2">
          <p className="text-sm text-destructive-text">History could not be loaded.</p>
          <Button variant="outline" size="lg" onClick={() => void history.refetch()}>
            Retry
          </Button>
        </div>
      ) : entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing saved yet — run a query and it shows up here.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {entries.map((entry) => (
            <li key={entry.id} className="rounded-sm border border-border p-3">
              {entry.prompt ? <p className="text-sm font-medium break-words">{entry.prompt}</p> : null}
              <p className="tabular text-xs text-muted-foreground">
                {whenLabel(entry.created_at)}
                {entry.runs > 1 ? ` · run ${entry.runs} times` : ""}
              </p>
              <pre className="mt-2 line-clamp-4 rounded-sm bg-muted p-2 font-mono text-xs break-words whitespace-pre-wrap">
                {entry.body}
              </pre>
              <div className="mt-2 flex gap-2">
                <Button
                  variant="outline"
                  size="lg"
                  onClick={() => {
                    onRestore(entry.body);
                    onClose();
                  }}
                >
                  Restore
                </Button>
                <Button variant="ghost" size="lg" onClick={() => void remove(entry.ids)}>
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </SideSheet>
  );
}
