"use client";

/** The saved threads: switch to one, or delete it. The list is fetched when the panel opens. */

import { useQuery } from "@tanstack/react-query";
import { MessagesSquare, Trash2 } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

import { fetchThreadList } from "../api";
import { threadLabel, threadPreview, threadRowMeta } from "../ask";
import { ToolbarPanel } from "./toolbar-panel";

export const THREADS_QUERY_KEY = ["ycs", "ask", "threads"] as const;

function ThreadList({
  currentId,
  onSwitch,
  onDelete,
}: {
  currentId: string;
  onSwitch: (id: string) => void;
  onDelete: (id: string) => void;
}): JSX.Element {
  const threads = useQuery({ queryKey: THREADS_QUERY_KEY, queryFn: fetchThreadList });
  if (threads.isPending) return <p className="px-2 py-1 text-xs text-muted-foreground">Loading threads…</p>;
  if (threads.isError) {
    return (
      <p role="alert" className="px-2 py-1 text-xs text-destructive-text">
        Could not load the threads.
      </p>
    );
  }
  if (threads.data.length === 0)
    return <p className="px-2 py-1 text-xs text-muted-foreground">No saved threads yet.</p>;
  return (
    <ul className="m-0 flex max-h-80 list-none flex-col gap-0.5 overflow-y-auto p-0">
      {threads.data.map((thread) => (
        <li
          key={thread.thread_id}
          className={`flex items-stretch rounded-sm ${thread.thread_id === currentId ? "bg-muted" : "hover:bg-muted/50"}`}
        >
          <button
            type="button"
            onClick={() => onSwitch(thread.thread_id)}
            aria-current={thread.thread_id === currentId ? "true" : undefined}
            className="min-h-9 min-w-0 flex-1 cursor-pointer rounded-sm px-2 py-1.5 text-left"
          >
            <span className="block text-sm leading-snug break-words">
              {threadPreview(thread.first_question)}
            </span>
            <span className="block text-xs text-muted-foreground">{threadRowMeta(thread)}</span>
          </button>
          <Button
            variant="ghost"
            size="icon-lg"
            onClick={() => onDelete(thread.thread_id)}
            aria-label={`Delete thread: ${threadPreview(thread.first_question)}`}
            className="self-center"
          >
            <Trash2 aria-hidden />
          </Button>
        </li>
      ))}
    </ul>
  );
}

export function AskThreads({
  threadId,
  title,
  onSwitch,
  onDelete,
}: {
  threadId: string;
  /** The current thread's first question; empty until it has turns on the server, when the button just says "New". */
  title: string;
  onSwitch: (id: string) => void;
  onDelete: (id: string) => void;
}): JSX.Element {
  return (
    <ToolbarPanel
      icon={<MessagesSquare aria-hidden />}
      label={`Thread: ${threadLabel(title)}`}
      align="phone-right"
    >
      {(close) => (
        <ThreadList
          currentId={threadId}
          onSwitch={(id) => {
            close();
            onSwitch(id);
          }}
          onDelete={onDelete}
        />
      )}
    </ToolbarPanel>
  );
}
