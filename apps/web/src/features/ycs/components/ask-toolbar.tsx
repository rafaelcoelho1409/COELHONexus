"use client";

/** The controls above the conversation: how hard to think (mode), which channels to search (scope), which thread this is, what
 * the thread has cost, and a fresh start. Mode and scope apply to the next question. On a phone it is two rows — the mode
 * with the two actions as icons, then channels and thread side by side — so the conversation starts high on the screen. */

import { BookOpen, Gauge, Plus } from "lucide-react";
import type { JSX } from "react";

import { ASK_MODES } from "../params";
import { ActionButton } from "./action-button";
import { AskScope } from "./ask-scope";
import { AskThreads } from "./ask-threads";

const MODE_HINT: Record<string, string> = {
  "": "Picks Fast, Standard or Deep for each question.",
  fast: "One quick pass over the best matches.",
  standard: "Retrieve, grade, then answer from what holds up.",
  deep: "Plans sub-questions, researches each, then merges them.",
};

export function AskToolbar({
  mode,
  onMode,
  scope,
  scopeLocked,
  onScope,
  threadId,
  threadTitle,
  onSwitchThread,
  onDeleteThread,
  onNewThread,
  onUsage,
  onSources,
}: {
  mode: string;
  onMode: (mode: string) => void;
  scope: readonly string[];
  scopeLocked: boolean;
  onScope: (ids: string[]) => void;
  threadId: string;
  /** The thread's first question, or empty while it has no saved turns ("New"). */
  threadTitle: string;
  onSwitchThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  onNewThread: () => void;
  onUsage: () => void;
  onSources: () => void;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end sm:gap-x-4">
      <div className="flex items-end gap-2 sm:contents">
        <fieldset className="m-0 min-w-0 flex-1 border-0 p-0 sm:flex-none">
          <legend className="mb-1 text-xs font-medium text-muted-foreground">Mode</legend>
          <div className="inline-flex max-w-full overflow-hidden rounded-sm border border-border bg-background">
            {ASK_MODES.map((option) => (
              <ActionButton
                key={option.id || "auto"}
                title={MODE_HINT[option.id]}
                type="button"
                aria-pressed={mode === option.id}
                onClick={() => onMode(option.id)}
                className="min-h-9 min-w-0 flex-1 rounded-none border-0 border-r border-r-border px-2 text-xs last:border-r-0 sm:flex-none sm:px-2.5"
              >
                {option.label}
              </ActionButton>
            ))}
          </div>
        </fieldset>
        <div className="flex gap-2 sm:order-last sm:ml-auto">
          <ActionButton type="button" onClick={onSources}>
            <BookOpen aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">Sources</span>
          </ActionButton>
          <ActionButton type="button" onClick={onUsage}>
            <Gauge aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">LLM usage</span>
          </ActionButton>
          <ActionButton type="button" onClick={onNewThread}>
            <Plus aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">New thread</span>
          </ActionButton>
        </div>
      </div>
      <div className="flex items-center gap-2 sm:contents">
        <div className="min-w-0 flex-1 sm:flex-none">
          <AskScope selected={scope} locked={scopeLocked} onChange={onScope} />
        </div>
        <div className="min-w-0 flex-1 sm:flex-none">
          <AskThreads
            threadId={threadId}
            title={threadTitle}
            onSwitch={onSwitchThread}
            onDelete={onDeleteThread}
          />
        </div>
      </div>
    </div>
  );
}
