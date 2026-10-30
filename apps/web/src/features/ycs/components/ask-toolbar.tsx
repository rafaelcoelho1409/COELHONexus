"use client";

/** The controls above the conversation: how hard to think (mode), which channels to search (scope), which thread this is, what
 * the thread has cost, and a fresh start. Mode and scope apply to the next question. On a phone it is two rows — the mode
 * with the two actions as icons, then channels and thread side by side — so the conversation starts high on the screen. */

import { Gauge, Plus } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

import { ASK_MODES } from "../params";
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
}): JSX.Element {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end sm:gap-x-4">
      <div className="flex items-end gap-2 sm:contents">
        <fieldset className="m-0 min-w-0 flex-1 border-0 p-0 sm:flex-none">
          <legend className="mb-1 text-xs font-medium text-muted-foreground">Mode</legend>
          <div className="flex">
            {ASK_MODES.map((option) => (
              <label
                key={option.id || "auto"}
                title={MODE_HINT[option.id]}
                className="relative -ml-px flex-1 first:ml-0 first:[&>span]:rounded-l-sm last:[&>span]:rounded-r-sm sm:flex-none"
              >
                <input
                  type="radio"
                  name="ask-mode"
                  value={option.id}
                  checked={mode === option.id}
                  onChange={() => onMode(option.id)}
                  className="peer sr-only"
                />
                <span className="flex min-h-9 cursor-pointer items-center justify-center border border-border px-2 text-sm text-muted-foreground hover:text-foreground peer-checked:border-primary peer-checked:bg-primary/10 peer-checked:text-primary-text peer-focus-visible:ring-2 peer-focus-visible:ring-ring sm:px-3">
                  {option.label}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex gap-2 sm:order-last sm:ml-auto">
          <Button type="button" variant="outline" size="lg" onClick={onUsage}>
            <Gauge aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">LLM usage</span>
          </Button>
          <Button type="button" variant="outline" size="lg" onClick={onNewThread}>
            <Plus aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">New thread</span>
          </Button>
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
