"use client";

/** The Query stage: a read-only workbench over the three stores. Backend tabs on top, then one card with the editor's
 * header, the assistant bar and the editor, and the results below at full width (a result table needs the room).
 * Each backend keeps its own draft and its own last result, so switching tabs loses nothing. Schema (which can insert a
 * starting query) and History open as side sheets. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type JSX, useId, useRef, useState } from "react";

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
import { BackendTabs, EditorHeader } from "./query-toolbar";

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
    <div className="flex flex-col gap-4">
      <div className="border-b border-border">
        <BackendTabs backend={backend} onChange={setBackend} />
      </div>

      <section
        aria-label="Editor"
        className="flex flex-col gap-3 rounded-md border border-border bg-card p-3"
      >
        <EditorHeader
          backend={backend}
          caption={labels.data?.[backend] ?? ""}
          status={runStatus(current)}
          running={running}
          onRun={run}
          onReset={() => setDraft(backend, QUERY_SCAFFOLDS[backend])}
          onSchema={() => setSheet("schema")}
          onHistory={() => setSheet("history")}
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

      <QueryResults key={`${backend}-${versions[backend]}`} backend={backend} state={current} />

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
  );
}
