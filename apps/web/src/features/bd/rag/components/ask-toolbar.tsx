"use client";

/** Third-row controls for the selected book, thread, and a fresh start. */

import { BookOpen, Plus } from "lucide-react";
import type { JSX } from "react";
import { LibraryPicker } from "../../components/library-list";
import { AskThreads } from "./ask-threads";
import { RagActionButton as Button } from "./rag-action-button";

export function AskToolbar({
  bookId,
  onOpenBook,
  threadId,
  threadTitle,
  onSwitchThread,
  onDeleteThread,
  onNewThread,
  onSources,
}: {
  bookId: string;
  onOpenBook: (bookId: string | null) => void;
  threadId: string;
  threadTitle: string;
  onSwitchThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  onNewThread: () => void;
  onSources: () => void;
}): JSX.Element {
  return (
    <div className="grid w-full min-w-0 grid-cols-2 items-end gap-2 sm:flex sm:flex-wrap sm:gap-x-4">
      <div className="min-w-0">
        <LibraryPicker selected={bookId} onOpen={onOpenBook} />
      </div>
      <div className="col-span-2 flex min-w-0 items-center justify-between gap-2 sm:ml-auto">
        <AskThreads
          bookId={bookId}
          threadId={threadId}
          title={threadTitle}
          onSwitch={onSwitchThread}
          onDelete={onDeleteThread}
        />
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="lg" onClick={onSources}>
            <BookOpen aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">Sources</span>
          </Button>
          <Button type="button" variant="outline" size="lg" onClick={onNewThread}>
            <Plus aria-hidden data-icon="inline-start" />
            <span className="sr-only sm:not-sr-only">New thread</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
