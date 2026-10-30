"use client";

/** The message box, pinned to the bottom of the page. Enter sends, Shift+Enter starts a new line, Escape stops a running
 * answer, and `/` anywhere on the page jumps here. The status line under it is the one polite live region of the stage. */

import { ArrowUp, Square } from "lucide-react";
import type { JSX, RefObject } from "react";
import { useLayoutEffect } from "react";

import { Button } from "@/shared/ui/button";

import type { AskStatus } from "../ask";

const MAX_HEIGHT_PX = 200;

export function AskComposer({
  value,
  onChange,
  onSend,
  onStop,
  running,
  status,
  inputRef,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: () => void;
  onStop: () => void;
  running: boolean;
  status: AskStatus;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}): JSX.Element {
  // Grow with the text up to a cap, then scroll. `value` is the trigger: the height must follow every edit.
  useLayoutEffect(() => {
    void value;
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value, inputRef]);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!running && value.trim()) onSend();
      }}
      className="sticky bottom-0 z-20 border-t border-border bg-background pt-3 pb-3"
    >
      <div className="flex items-end gap-2">
        <label htmlFor="ask-input" className="sr-only">
          Ask about your videos
        </label>
        <textarea
          id="ask-input"
          ref={inputRef}
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!running && value.trim()) onSend();
            } else if (event.key === "Escape" && running) {
              event.preventDefault();
              onStop();
            }
          }}
          placeholder="Ask about your videos…"
          className="min-h-10 flex-1 resize-none rounded-sm border border-input bg-background px-3 py-2 text-sm leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        {running ? (
          <Button type="button" variant="outline" size="lg" onClick={onStop} className="h-10">
            <Square aria-hidden data-icon="inline-start" />
            Stop
          </Button>
        ) : (
          <Button type="submit" size="lg" disabled={!value.trim()} className="h-10">
            <ArrowUp aria-hidden data-icon="inline-start" />
            Send
          </Button>
        )}
      </div>
      <div className="mt-1.5 flex min-h-5 flex-wrap items-baseline justify-between gap-x-4 text-xs text-muted-foreground">
        <p
          role="status"
          aria-live="polite"
          className={`m-0 ${status.tone === "error" ? "text-destructive-text" : ""} ${status.tone === "running" ? "animate-status" : ""}`}
        >
          {status.text}
        </p>
        <p className="m-0 hidden sm:block">
          Enter to send · Shift+Enter for a new line · Esc to stop · / to focus
        </p>
      </div>
    </form>
  );
}
