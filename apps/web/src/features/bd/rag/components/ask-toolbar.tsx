"use client";

/** Controls above the book conversation: book, reasoning mode, thread, usage, and a fresh start. */

import { BookOpen, Gauge, Plus } from "lucide-react";
import type { JSX } from "react";
import { LibraryPicker } from "../../components/library-list";
import { ASK_MODES } from "../params";
import { AskThreads } from "./ask-threads";
import { RagActionButton as Button } from "./rag-action-button";

const MODE_HINT: Record<string, string> = {
  "": "Picks Fast, Standard or Deep for each question.",
  fast: "One quick pass over the best matches.",
  standard: "Retrieve, grade, then answer from what holds up.",
  deep: "Plans sub-questions, researches each, then merges them.",
};

export function AskToolbar({
  mode,
  onMode,
  bookId,
  onOpenBook,
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
  bookId: string;
  onOpenBook: (bookId: string | null) => void;
  threadId: string;
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
          <div className="flex">
            {ASK_MODES.map((option) => (
              <label
                key={option.id || "auto"}
                title={MODE_HINT[option.id]}
                className="relative -ml-px flex-1 first:ml-0 first:[&>span]:rounded-l-sm last:[&>span]:rounded-r-sm sm:flex-none"
              >
                <input
                  type="radio"
                  name="bd-ask-mode"
                  value={option.id}
                  checked={mode === option.id}
                  onChange={() => onMode(option.id)}
                  className="peer sr-only"
                />
                <span className="flex min-h-10 cursor-pointer items-center justify-center rounded-none border border-border bg-background px-2 text-sm font-medium text-foreground hover:border-neutral-8 peer-checked:border-primary peer-checked:bg-muted peer-focus-visible:ring-3 peer-focus-visible:ring-ring/50 sm:px-3">
                  {option.label}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex gap-2 sm:order-last sm:ml-auto">
          <Button type="button" variant="outline" size="lg" onClick={onSources}>
            <BookOpen aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">Sources</span>
          </Button>
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
          <LibraryPicker selected={bookId} showActions={false} onOpen={onOpenBook} />
        </div>
        <div className="min-w-0 flex-1 sm:flex-none">
          <AskThreads
            bookId={bookId}
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
