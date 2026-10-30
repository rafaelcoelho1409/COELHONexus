"use client";

/** What the current backend holds — indices and fields, collections and payload keys, or node labels and
 * relationships — beside the editor, so a query can be written without guessing names. A store with many groups (Neo4j
 * lists dozens of labels) starts folded behind a filter box; every group and every field with a sensible starting query
 * has an "Insert query" button that puts that query in the editor. */

import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { type JSX, useId, useState } from "react";

import { Button } from "@/shared/ui/button";

import { fetchQuerySchema } from "../api";
import { QUERY_BACKENDS, QUERY_SCHEMA_OPEN_UNDER, type QueryBackend } from "../params";
import { filterSchema, type SchemaGroup, schemaOutline } from "../query";
import { SideSheet } from "./side-sheet";

/** The server caches a schema for five minutes, so asking again sooner returns the same thing. */
const SCHEMA_STALE_MS = 5 * 60 * 1000;

function Group({
  group,
  open,
  onToggle,
  onInsert,
}: {
  group: SchemaGroup;
  open: boolean;
  onToggle: () => void;
  onInsert: (query: string) => void;
}): JSX.Element {
  return (
    <div className="rounded-sm border border-border">
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className="flex min-h-9 min-w-0 flex-1 items-center gap-1 rounded-sm px-2 text-left hover:bg-muted"
        >
          <ChevronRight aria-hidden className={`size-4 shrink-0 ${open ? "rotate-90" : ""}`} />
          <span className="min-w-0 font-mono text-sm font-semibold break-all">{group.title}</span>
          {group.note ? <span className="shrink-0 text-xs text-muted-foreground">{group.note}</span> : null}
        </button>
        {group.query ? (
          <Button
            variant="ghost"
            size="lg"
            onClick={() => onInsert(group.query ?? "")}
            aria-label={`Insert query for ${group.title}`}
          >
            Insert query
          </Button>
        ) : null}
      </div>
      {open ? (
        group.items.length > 0 ? (
          <ul className="divide-y divide-border border-t border-border">
            {group.items.map((item) => (
              <li
                key={item.name}
                className="flex flex-wrap items-center justify-between gap-x-3 px-3 py-1 pl-8"
              >
                <span className="min-w-0 font-mono text-sm break-all">{item.name}</span>
                <span className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{item.detail}</span>
                  {item.query ? (
                    <Button
                      variant="ghost"
                      size="lg"
                      onClick={() => onInsert(item.query ?? "")}
                      aria-label={`Insert query for ${item.name}`}
                    >
                      Insert
                    </Button>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">No fields listed.</p>
        )
      ) : null}
    </div>
  );
}

export function QuerySchemaSheet({
  backend,
  onInsert,
  onClose,
}: {
  backend: QueryBackend;
  /** Puts a query in the editor; the sheet closes after it. */
  onInsert: (query: string) => void;
  onClose: () => void;
}): JSX.Element {
  const searchId = useId();
  const [text, setText] = useState("");
  // A group the reader opened or folded by hand; the rest follow the default (open for a few groups, or while filtering).
  const [toggled, setToggled] = useState<ReadonlySet<string>>(new Set());
  const schema = useQuery({
    queryKey: ["ycs", "query", "schema", backend],
    queryFn: () => fetchQuerySchema(backend),
    staleTime: SCHEMA_STALE_MS,
  });
  const label = QUERY_BACKENDS.find((entry) => entry.id === backend)?.label ?? backend;
  const all = schema.data ? schemaOutline(backend, schema.data) : [];
  const groups = filterSchema(all, text);
  const filtering = text.trim() !== "";
  const openByDefault = filtering || all.length < QUERY_SCHEMA_OPEN_UNDER;

  const toggle = (title: string) =>
    setToggled((current) => {
      const next = new Set(current);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      return next;
    });

  return (
    <SideSheet title="Schema" subtitle={label} closeLabel="Close schema" onClose={onClose}>
      {schema.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading schema…
        </p>
      ) : schema.isError ? (
        <div role="alert" className="flex flex-col items-start gap-2">
          <p className="text-sm text-destructive-text">The schema could not be loaded.</p>
          <Button variant="outline" size="lg" onClick={() => void schema.refetch()}>
            Retry
          </Button>
        </div>
      ) : all.length === 0 ? (
        <p className="text-sm text-muted-foreground">The server returned no schema for this store.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={searchId} className="text-xs text-muted-foreground">
              Filter the schema
            </label>
            <input
              id={searchId}
              type="search"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="A name, a field…"
              className="min-h-9 w-full rounded-sm border border-input bg-background px-3 text-sm"
            />
            <p role="status" className="tabular text-xs text-muted-foreground">
              {filtering
                ? `${groups.length} of ${all.length} match`
                : `${all.length} ${all.length === 1 ? "group" : "groups"}`}
            </p>
          </div>
          {groups.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing matches that.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {groups.map((group) => (
                <Group
                  key={group.title}
                  group={group}
                  open={openByDefault !== toggled.has(group.title)}
                  onToggle={() => toggle(group.title)}
                  onInsert={(query) => {
                    onInsert(query);
                    onClose();
                  }}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </SideSheet>
  );
}
