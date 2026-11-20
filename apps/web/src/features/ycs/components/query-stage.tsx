"use client";

/** The Query stage: a read-only workbench over the three stores. After a run, the editor and results share a 2:3 split.
 * Each backend keeps its own draft and result; Schema and History open as side sheets. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type JSX, useId, useRef, useState } from "react";

import { ContextBar } from "@/shared/layout/context-bar";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { fetchQueryLabels, runRawQuery, saveQueryHistory } from "../api";
import { useQueryAi } from "../hooks/use-query-ai";
import { QUERY_BACKENDS, QUERY_SCAFFOLDS, type QueryBackend } from "../params";
import { type RunState, runStateOf, runStatus } from "../query";
import { QueryAiBar } from "./query-ai-bar";
import { QueryEditor } from "./query-editor";
import { QueryHistorySheet, queryHistoryKey } from "./query-history-sheet";
import { QueryResults } from "./query-results";
import { QuerySchemaSheet } from "./query-schema-sheet";
import { BackendTabs, EditorActions, EditorHeader } from "./query-toolbar";

type PerBackend<T> = Record<QueryBackend, T>;

const IDLE: RunState = { kind: "idle" };
const EDITOR_LABEL = "Query editor";

export function QueryStage(): JSX.Element {
  const queryClient = useQueryClient();
  const hintId = useId();
  const [backend, setBackend] = useState<QueryBackend>("elasticsearch");
  const [drafts, setDrafts] = useState<PerBackend<string>>(QUERY_SCAFFOLDS);
  const [runs, setRuns] = useState<PerBackend<RunState>>({ elasticsearch: IDLE, qdrant: IDLE, neo4j: IDLE });
  // A new result gets a new key, so its table starts unsorted and unfiltered.
  const [versions, setVersions] = useState<PerBackend<number>>({ elasticsearch: 0, qdrant: 0, neo4j: 0 });
  const [prompt, setPrompt] = useState("");
  const [aiBackend, setAiBackend] = useState<QueryBackend | null>(null);
  const [sheet, setSheet] = useState<"schema" | "history" | null>(null);
  const ai = useQueryAi();
  // What each backend last saved to the history, so running the same query again does not add a second entry.
  const lastSaved = useRef<Partial<Record<QueryBackend, string>>>({});

  const labels = useQuery({
    queryKey: ["ycs", "query", "labels"],
    queryFn: fetchQueryLabels,
    staleTime: Infinity,
  });

  const setRun = (which: QueryBackend, state: RunState) =>
    setRuns((current) => ({ ...current, [which]: state }));
  const setDraft = (which: QueryBackend, text: string) =>
    setDrafts((current) => ({ ...current, [which]: text }));

  const execute = useMutation({
    mutationFn: (job: { backend: QueryBackend; body: string }) => runRawQuery(job.backend, job.body),
    onMutate: (job) => setRun(job.backend, { kind: "running" }),
    onSuccess: (result, job) => {
      setVersions((current) => ({ ...current, [job.backend]: current[job.backend] + 1 }));
      setRun(job.backend, runStateOf(result));
      if (!result.ok) return;
      const saved = `${prompt}\u0000${job.body}`;
      if (lastSaved.current[job.backend] === saved) return;
      lastSaved.current[job.backend] = saved;
      void saveQueryHistory(job.backend, job.body, prompt).then(() =>
        queryClient.invalidateQueries({ queryKey: queryHistoryKey(job.backend) }),
      );
    },
    onError: (error, job) =>
      setRun(job.backend, { kind: "failed", message: apiErrorMessage(error), notes: [] }),
  });

  const run = () => {
    const body = drafts[backend];
    if (!body.trim()) {
      setRun(backend, { kind: "failed", message: "The editor is empty.", notes: [] });
      return;
    }
    execute.mutate({ backend, body });
  };

  const generate = () => {
    const text = prompt.trim();
    if (!text) return;
    const target = backend;
    setAiBackend(target);
    ai.start({
      backend: target,
      prompt: text,
      previous: drafts[target],
      onText: (next) => setDraft(target, next),
    });
  };

  const current = runs[backend];
  const running = current.kind === "running";
  const entry = QUERY_BACKENDS.find((candidate) => candidate.id === backend);

  return (
    <>
      <ContextBar pinned end={<BackendTabs backend={backend} onChange={setBackend} />} />
      <div className="flex flex-col gap-4">
        <div
          className={`grid min-w-0 grid-cols-1 items-start gap-4 ${
            current.kind === "idle" ? "" : "xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
          }`}
        >
          <section
            aria-label="Query editor"
            className="flex min-w-0 flex-col gap-3 rounded-md border border-border bg-card p-3"
          >
            <EditorHeader
              backend={backend}
              caption={labels.data?.[backend] ?? ""}
              status={runStatus(current)}
            />
            <QueryAiBar
              prompt={prompt}
              onPrompt={setPrompt}
              state={ai.state}
              running={ai.running}
              onGenerate={generate}
              onStop={ai.stop}
              onRun={run}
            />
            <EditorActions
              backendLabel={entry?.label ?? backend}
              queryRunning={running}
              queryDisabled={ai.running}
              onReset={() => setDraft(backend, QUERY_SCAFFOLDS[backend])}
              onSchema={() => setSheet("schema")}
              onHistory={() => setSheet("history")}
              onRun={run}
            />
            <div className="flex flex-col gap-1.5">
              {/* One editor per backend: its undo history never reaches back into another store's query. */}
              <QueryEditor
                key={backend}
                value={drafts[backend]}
                language={entry?.language ?? "json"}
                label={EDITOR_LABEL}
                describedBy={hintId}
                locked={ai.running && aiBackend === backend}
                onChange={(text) => setDraft(backend, text)}
                onRun={run}
              />
              <p id={hintId} className="text-xs text-muted-foreground">
                Ctrl/Cmd+Enter runs. Tab indents; press Escape, then Tab, to move focus out of the editor.
              </p>
            </div>
          </section>

          {current.kind !== "idle" ? (
            <section
              aria-label="Query results"
              className="min-w-0 rounded-md border border-border bg-card p-3"
            >
              <QueryResults key={`${backend}-${versions[backend]}`} backend={backend} state={current} />
            </section>
          ) : null}
        </div>

        {sheet === "schema" ? (
          <QuerySchemaSheet
            backend={backend}
            onInsert={(query) => setDraft(backend, query)}
            onClose={() => setSheet(null)}
          />
        ) : null}
        {sheet === "history" ? (
          <QueryHistorySheet
            backend={backend}
            onRestore={(body) => setDraft(backend, body)}
            onDeleted={() => {
              lastSaved.current = {};
            }}
            onClose={() => setSheet(null)}
          />
        ) : null}
      </div>
    </>
  );
}
