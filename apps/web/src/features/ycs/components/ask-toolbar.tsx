"use client";

/** Conversation controls for source scope, thread history, citations, and a fresh thread. */

import { BookOpen, Plus } from "lucide-react";
import type { JSX } from "react";

import { ActionButton } from "./action-button";
import { AskScope } from "./ask-scope";
import { AskThreads } from "./ask-threads";

export function AskToolbar({
  scope,
  scopeLocked,
  onScope,
  threadId,
  threadTitle,
  onSwitchThread,
  onDeleteThread,
  onNewThread,
  onSources,
}: {
  scope: readonly string[];
  scopeLocked: boolean;
  onScope: (ids: string[]) => void;
  threadId: string;
  /** The thread's first question, or empty while it has no saved turns ("New"). */
  threadTitle: string;
  onSwitchThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  onNewThread: () => void;
  onSources: () => void;
}): JSX.Element {
  return (
    <div className="grid w-full min-w-0 grid-cols-2 items-end gap-2 sm:flex sm:flex-wrap sm:gap-x-4">
      <div className="min-w-0">
        <AskScope selected={scope} locked={scopeLocked} onChange={onScope} />
      </div>
      <div className="col-span-2 flex min-w-0 items-center justify-between gap-2 sm:ml-auto">
        <AskThreads
          threadId={threadId}
          title={threadTitle}
          onSwitch={onSwitchThread}
          onDelete={onDeleteThread}
        />
        <div className="flex gap-2">
          <ActionButton type="button" onClick={onSources}>
            <BookOpen aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">Sources</span>
          </ActionButton>
          <ActionButton type="button" onClick={onNewThread}>
            <Plus aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">New thread</span>
          </ActionButton>
        </div>
      </div>
    </div>
  );
}
