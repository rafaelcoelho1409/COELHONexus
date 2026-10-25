"use client";

/** A side sheet for one graph node: what it does and what it produced (Overview), the stream events it
 * emitted (Activity), and the raw state it reads and writes (Raw I/O). It stays live while the run goes on. */

import { useEffect, useRef, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";

import { fmtClock } from "../domain/format";
import { eventSummary } from "../domain/live-text";
import { nodeDetails, nodeMetrics, nodeRawIO } from "../domain/node-details";
import { buildOverview } from "../domain/overview";
import type { StageNodeView } from "../domain/stages";
import { nodeUsageMetrics } from "../domain/usage";
import type { StageEvent } from "../entities";
import { useRunUsage } from "../hooks/use-usage";
import type { RunStage } from "../params";
import { NodeOverviewView } from "./node-overview";
import { SideSheet } from "./side-sheet";
import { NodeStatusBadge } from "./stage-parts";

type TabId = "overview" | "activity" | "raw";

const TAB_IDS: readonly TabId[] = ["overview", "activity", "raw"];
const isTabId = (value: unknown): value is TabId => TAB_IDS.some((id) => id === value);

const PENDING_NOTE: Record<StageNodeView["status"], string | null> = {
  done: null,
  failed: "This node failed before producing output. The Activity tab has the event trace.",
  running: "Running — its results appear here the moment the node commits.",
  pending: "This node has not run yet.",
  future: "The server does not implement this node yet.",
};

function Pills({ items }: { items: string[] }) {
  return (
    <span className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <code key={item} className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-xs">
          {item}
        </code>
      ))}
    </span>
  );
}

/** How close to the bottom (px) the log must be for new lines to keep it following the tail. */
const PINNED_SLACK_PX = 24;

function ActivityLog({ stage, events }: { stage: RunStage; events: StageEvent[] }) {
  const box = useRef<HTMLOListElement>(null);
  const pinned = useRef(true); // false once the reader scrolls up, so new lines don't yank the view away

  // Follow the newest line like a terminal tail, unless the reader scrolled up.
  useEffect(() => {
    const element = box.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  });

  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">No events for this node in this session yet.</p>;
  }
  // Events carry no id, so number them once; the log is append-only and bounded.
  const lines = events.map((event, id) => ({ id, event }));
  return (
    <ol
      ref={box}
      role="log"
      aria-label="Node events"
      // biome-ignore lint/a11y/noNoninteractiveTabindex: the log scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
      tabIndex={0}
      onScroll={() => {
        const element = box.current;
        if (element) {
          pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < PINNED_SLACK_PX;
        }
      }}
      className="max-h-96 overflow-auto rounded-sm border border-border"
    >
      {lines.map(({ id, event }) => (
        <li
          key={id}
          className="flex gap-3 border-b border-border px-3 py-1.5 font-mono text-xs last:border-b-0"
        >
          <span className="shrink-0 text-muted-foreground">{fmtClock(event.ts)}</span>
          <span className="break-all">{eventSummary(stage, event)}</span>
        </li>
      ))}
    </ol>
  );
}

function RawBlock({ title, json }: { title: string; json: string }) {
  return (
    <details className="rounded-sm border border-border" open>
      <summary className="min-h-9 cursor-pointer px-3 py-2 text-sm font-medium">{title}</summary>
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: long JSON scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1) */}
      <pre tabIndex={0} className="max-h-96 overflow-auto border-t border-border px-3 py-2 font-mono text-xs">
        {json}
      </pre>
    </details>
  );
}

export function NodeDrawer({
  stage,
  view,
  values,
  events,
  threadId,
  live,
  onClose,
}: {
  stage: RunStage;
  view: StageNodeView;
  values: Record<string, unknown>;
  events: StageEvent[];
  /** The run thread whose LLM usage this node's numbers come from. */
  threadId: string | null;
  live: boolean;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<TabId>("overview");
  const [seen, setSeen] = useState(0);
  const usage = useRunUsage(stage, threadId, { live });

  const { node } = view;
  const details = nodeDetails(stage, node.id);
  const nodeEvents = events.filter((event) => event.step === node.id);
  const unseen = tab === "activity" ? 0 : Math.max(0, nodeEvents.length - seen);
  const reported = view.status === "done" || view.status === "running";
  const metrics = reported
    ? [...nodeMetrics(stage, node.id, values), ...nodeUsageMetrics(usage, node.id)]
    : [];
  const raw = nodeRawIO(stage, node.id, values);
  const pendingNote = PENDING_NOTE[view.status];

  const changeTab = (next: TabId) => {
    // Leaving or entering Activity marks everything so far as seen.
    if (tab === "activity" || next === "activity") setSeen(nodeEvents.length);
    setTab(next);
  };

  return (
    <SideSheet
      title={details?.title ?? node.label}
      closeLabel="Close node details"
      onClose={onClose}
      subtitle={
        <>
          <NodeStatusBadge status={view.status} />
          {details ? <span className="font-mono text-xs text-muted-foreground">{details.kind}</span> : null}
        </>
      }
    >
      <Tabs
        value={tab}
        onValueChange={(next) => {
          if (isTabId(next)) changeTab(next);
        }}
      >
        <TabsList aria-label="Node details">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="activity">
            Activity
            {unseen > 0 ? (
              <span className="rounded-sm bg-muted px-1.5 text-xs text-foreground">
                <span aria-hidden>{unseen}</span>
                <span className="sr-only">{`, ${unseen} new`}</span>
              </span>
            ) : null}
          </TabsTrigger>
          <TabsTrigger value="raw">Raw I/O</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex flex-col gap-5">
          {details ? (
            <section className="flex flex-col gap-3">
              <p className="text-sm">{details.subtitle}</p>
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {details.actions.map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
              <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
                <span>Reads</span>
                <Pills items={details.inputs} />
                <span>Writes</span>
                <Pills items={details.outputs} />
                <span>LLM</span>
                <span className="text-sm">{details.llm}</span>
              </div>
            </section>
          ) : null}
          {metrics.length > 0 ? (
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {metrics.map((metric) => (
                <div key={metric.label} className="rounded-sm border border-border px-3 py-2">
                  <dt className="text-xs text-muted-foreground">{metric.label}</dt>
                  <dd className="text-sm font-medium tabular-nums">{metric.value}</dd>
                  {metric.note ? <dd className="text-xs text-muted-foreground">{metric.note}</dd> : null}
                </div>
              ))}
            </dl>
          ) : null}
          {pendingNote && view.status !== "running" ? (
            <p className="text-sm text-muted-foreground">{pendingNote}</p>
          ) : (
            <NodeOverviewView overview={buildOverview(stage, node.id, values)} />
          )}
        </TabsContent>

        <TabsContent value="activity">
          <ActivityLog stage={stage} events={nodeEvents} />
        </TabsContent>

        <TabsContent value="raw" className="flex flex-col gap-3">
          {raw.inputs === null && raw.outputs === null ? (
            <p className="text-sm text-muted-foreground">
              Raw inputs and outputs appear here once this node and the ones before it have committed.
            </p>
          ) : null}
          {raw.inputs !== null ? <RawBlock title="Inputs (upstream state, raw)" json={raw.inputs} /> : null}
          {raw.outputs !== null ? <RawBlock title="Outputs (this node, raw)" json={raw.outputs} /> : null}
        </TabsContent>
      </Tabs>
    </SideSheet>
  );
}
