"use client";

import type { AskTurn } from "../ask";
import type { AskAgenticNode, AskAgenticNodeStatus } from "./ask-agentic-types";
import { SideSheet } from "./side-sheet";
import { StatusBadge } from "./status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";

const STATUS: Record<
  AskAgenticNodeStatus,
  { tone: "queued" | "running" | "done" | "failed" | "na"; label: string }
> = {
  queued: { tone: "queued", label: "Queued" },
  active: { tone: "running", label: "Running" },
  done: { tone: "done", label: "Done" },
  failed: { tone: "failed", label: "Failed" },
  skipped: { tone: "na", label: "Not selected" },
};

const NODE_LABELS: Readonly<Record<string, string>> = {
  prepare: "Prepare and route question",
  contextualize: "Prepare and route question",
  classify_query: "Prepare and route question",
  direct_answer: "Direct answer",
  run_standard: "Standard video RAG",
  plan_research: "Plan research",
  run_subagents: "Research sub-questions",
  run_subagent: "Research sub-questions",
  synthesize: "Synthesize findings",
  critic: "Review answer support",
};

const RETRIEVAL_SOURCE_LABELS: Readonly<Record<string, string>> = {
  qdrant_hybrid: "Qdrant hybrid search",
  neo4j_graph: "Neo4j graph search",
  elasticsearch: "Elasticsearch full-text search",
};

function visitedLabels(turn: AskTurn | null): string[] {
  return (turn?.graph.visited_nodes ?? []).map((nodeId) => NODE_LABELS[nodeId] ?? nodeId);
}

function latestActivity(node: AskAgenticNode, turn: AskTurn | null): string {
  if (node.activity) return node.activity;
  if (!turn) return "This node has not run yet.";
  if (node.status === "skipped") return "This route was not selected for the current turn.";
  if (node.status === "queued") return "Waiting for this node to run.";
  if (node.status === "failed") return "The current turn failed in this node or its route.";
  if (node.status === "active") return "This node is processing the current question.";
  return "This node completed in the current turn.";
}

function ResourceSection({ label, entries }: { label: string; entries: readonly string[] }) {
  return (
    <section className="rounded-sm border border-border bg-card p-3">
      <h3 className="text-sm font-medium">{label}</h3>
      {entries.length > 0 ? (
        <ul className="mt-2 mb-0 flex list-none flex-col gap-1.5 p-0">
          {entries.map((entry) => (
            <li key={entry} className="break-words text-sm text-muted-foreground">
              {entry}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 mb-0 text-sm text-muted-foreground">None assigned to this node.</p>
      )}
    </section>
  );
}

export function AskAgenticNodeSheet({
  node,
  turn,
  onClose,
}: {
  node: AskAgenticNode;
  turn: AskTurn | null;
  onClose: () => void;
}) {
  const state = STATUS[node.status];
  const route = turn?.graph.route || turn?.mode || "Not chosen";
  const visited = visitedLabels(turn);
  const retrievalSources = (turn?.graph.retrieval_sources ?? []).map(
    (source) => RETRIEVAL_SOURCE_LABELS[source] ?? source,
  );

  return (
    <SideSheet
      key={node.id}
      title={node.label}
      subtitle={<StatusBadge tone={state.tone} label={state.label} />}
      closeLabel="Close Ask node details"
      onClose={onClose}
    >
      <Tabs defaultValue="overview" className="w-full">
        <TabsList aria-label={`${node.label} details`}>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="resources">Resources</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex flex-col gap-3 pt-4">
          <section aria-label="Node purpose" className="rounded-sm border border-border bg-card p-3">
            <h3 className="text-sm font-medium">What this node does</h3>
            <p className="mt-2 mb-0 text-sm text-muted-foreground">{node.description}</p>
          </section>
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div className="rounded-sm border border-border px-3 py-2">
              <dt className="text-xs text-muted-foreground">LangGraph node</dt>
              <dd className="mt-1 break-words font-mono text-xs">{node.id}</dd>
            </div>
            <div className="rounded-sm border border-border px-3 py-2">
              <dt className="text-xs text-muted-foreground">Selected route</dt>
              <dd className="mt-1 break-words text-sm">{route}</dd>
            </div>
          </dl>
          <section aria-label="Latest node activity" className="rounded-sm border border-border bg-card p-3">
            <h3 className="text-sm font-medium">Latest update</h3>
            <p className="mt-2 mb-0 break-words text-sm text-muted-foreground">
              {latestActivity(node, turn)}
            </p>
          </section>
        </TabsContent>

        <TabsContent value="resources" className="flex flex-col gap-3 pt-4">
          <ResourceSection label="Agents" entries={node.resources.agents} />
          <ResourceSection label="Skills" entries={node.resources.skills} />
          <ResourceSection label="Tools" entries={node.resources.tools} />
        </TabsContent>

        <TabsContent value="activity" className="flex flex-col gap-3 pt-4">
          <section aria-label="Execution trace" className="rounded-sm border border-border bg-card p-3">
            <h3 className="text-sm font-medium">Nodes visited</h3>
            {visited.length > 0 ? (
              <ol className="mt-2 mb-0 flex list-none flex-col gap-1.5 p-0">
                {visited.map((label, index) => (
                  <li key={`${label}-${index}`} className="text-sm text-muted-foreground">
                    {index + 1}. {label}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-2 mb-0 text-sm text-muted-foreground">No nodes have run for this turn yet.</p>
            )}
          </section>
          {retrievalSources.length > 0 ? (
            <section aria-label="Retrieval sources" className="rounded-sm border border-border bg-card p-3">
              <h3 className="text-sm font-medium">Retrieval sources</h3>
              <ul className="mt-2 mb-0 flex list-none flex-col gap-1.5 p-0">
                {retrievalSources.map((source) => (
                  <li key={source} className="text-sm text-muted-foreground">
                    {source}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {node.id === "run_subagents" && (turn?.deep?.cards.length ?? 0) > 0 ? (
            <section
              aria-label="Research sub-questions"
              className="rounded-sm border border-border bg-card p-3"
            >
              <h3 className="text-sm font-medium">Research sub-questions</h3>
              <ol className="mt-2 mb-0 flex list-none flex-col gap-2 p-0">
                {turn?.deep?.cards.map((card, index) => (
                  <li key={`${card.question}-${index}`} className="border-l-2 border-border pl-2.5">
                    <p className="m-0 text-sm">{card.question}</p>
                    <StatusBadge
                      className="mt-1"
                      tone={card.status === "done" ? "done" : "queued"}
                      label={card.status === "done" ? "Done" : "Queued"}
                    />
                    {card.answer ? (
                      <p className="mt-1 mb-0 text-xs text-muted-foreground">{card.answer}</p>
                    ) : null}
                  </li>
                ))}
              </ol>
            </section>
          ) : null}
        </TabsContent>
      </Tabs>
    </SideSheet>
  );
}
