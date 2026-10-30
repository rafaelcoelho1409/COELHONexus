"use client";

/** The controls above the conversation: how hard to think (mode), which channels to search (scope), which thread this is, what
 * the thread has cost, and a fresh start. Mode and scope apply to the next question. */

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
  threadSaved,
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
  threadSaved: boolean;
  onSwitchThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  onNewThread: () => void;
  onUsage: () => void;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
      <fieldset className="m-0 border-0 p-0">
        <legend className="mb-1 text-xs font-medium text-muted-foreground">Mode</legend>
        <div className="flex">
          {ASK_MODES.map((option) => (
            <label
              key={option.id || "auto"}
              title={MODE_HINT[option.id]}
              className="relative -ml-px first:ml-0 first:[&>span]:rounded-l-sm last:[&>span]:rounded-r-sm"
            >
              <input
                type="radio"
                name="ask-mode"
                value={option.id}
                checked={mode === option.id}
                onChange={() => onMode(option.id)}
                className="peer sr-only"
              />
              <span className="flex min-h-9 cursor-pointer items-center border border-border px-3 text-sm text-muted-foreground hover:text-foreground peer-checked:border-primary peer-checked:bg-primary/10 peer-checked:text-primary-text peer-focus-visible:ring-2 peer-focus-visible:ring-ring">
                {option.label}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <AskScope selected={scope} locked={scopeLocked} onChange={onScope} />
      <AskThreads
        threadId={threadId}
        saved={threadSaved}
        onSwitch={onSwitchThread}
        onDelete={onDeleteThread}
      />
      <div className="ml-auto flex gap-2">
        <Button type="button" variant="outline" size="lg" onClick={onUsage}>
          <Gauge aria-hidden data-icon="inline-start" />
          LLM usage
        </Button>
        <Button type="button" variant="outline" size="lg" onClick={onNewThread}>
          <Plus aria-hidden data-icon="inline-start" />
          New thread
        </Button>
      </div>
    </div>
  );
}
