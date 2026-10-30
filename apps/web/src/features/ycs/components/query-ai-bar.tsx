"use client";

/** The assistant bar above the editor, one row tall: describe the data you want, and the query it writes lands in the
 * editor (which stays editable afterwards). The box grows with a longer request. Ctrl/Cmd+Enter generates,
 * Ctrl/Cmd+Shift+Enter runs what is in the editor, Escape stops; one polite line reports progress. */

import { Sparkles, Square } from "lucide-react";
import { type JSX, type KeyboardEvent, useId } from "react";

import { Button } from "@/shared/ui/button";

import { QUERY_AI_PLACEHOLDER } from "../params";
import type { AiState } from "../query";

export function QueryAiBar({
  prompt,
  onPrompt,
  state,
  running,
  onGenerate,
  onStop,
  onRun,
}: {
  prompt: string;
  onPrompt: (text: string) => void;
  state: AiState;
  running: boolean;
  onGenerate: () => void;
  onStop: () => void;
  onRun: () => void;
}): JSX.Element {
  const id = useId();

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Escape" && running) {
      event.preventDefault();
      onStop();
      return;
    }
    if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    if (event.shiftKey) onRun();
    else if (!running) onGenerate();
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
        <label htmlFor={id} className="mr-auto text-xs text-muted-foreground">
          Describe the query
        </label>
        {/* Keyboard shortcuts mean little on a phone, so the line gives its row back there (it still names the box). */}
        <p id={`${id}-hint`} className="hidden text-xs text-muted-foreground sm:block">
          Ctrl/Cmd+Enter generates · Ctrl/Cmd+Shift+Enter runs · Esc stops
        </p>
        <p
          role="status"
          className={`text-xs ${state.phase === "error" ? "text-destructive-text" : "text-muted-foreground"}`}
        >
          {state.message}
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <textarea
          id={id}
          value={prompt}
          onChange={(event) => onPrompt(event.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder={QUERY_AI_PLACEHOLDER}
          aria-describedby={`${id}-hint`}
          className="field-sizing-content max-h-40 min-h-9 w-full resize-none rounded-sm border border-input bg-background px-3 py-2 text-sm"
        />
        {running ? (
          <Button variant="outline" size="lg" onClick={onStop}>
            <Square aria-hidden /> Stop
          </Button>
        ) : (
          <Button size="lg" onClick={onGenerate} disabled={!prompt.trim()}>
            <Sparkles aria-hidden /> Generate
          </Button>
        )}
      </div>
    </div>
  );
}
