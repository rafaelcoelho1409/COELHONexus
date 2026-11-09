"use client";

/** The "Thinking" trace of one turn: the four pipeline steps with what each is doing, and — in Deep mode — the research plan
 * and a card per sub-question. Open while the turn runs, folded to "Thought for 14s" once it is done; the reader can flip it
 * either way, and a choice made stays. */

import { ChevronDown } from "lucide-react";
import type { JSX } from "react";
import { useId, useState } from "react";

import { Markdown } from "@/shared/markdown";

import { type AskDeep, type AskStageId, type AskStageStatus, type AskTurn, thinkingLabel } from "../ask";
import { ASK_STAGES } from "../params";
import { type BadgeTone, StatusBadge } from "./status-badge";

const TONE: Record<AskStageStatus, BadgeTone> = { queued: "queued", active: "running", done: "done" };
const WORD: Record<AskStageStatus, string> = { queued: "waiting", active: "running", done: "done" };

function Steps({ stages }: { stages: AskTurn["stages"] }): JSX.Element {
  return (
    <ol className="m-0 grid list-none grid-cols-2 gap-x-4 gap-y-2 p-0 sm:grid-cols-4">
      {ASK_STAGES.map((stage) => {
        const view = stages[stage.id as AskStageId];
        return (
          <li key={stage.id} className="min-w-0">
            <StatusBadge tone={TONE[view.status]} label={stage.label} />
            <span className="sr-only"> — {WORD[view.status]}</span>
            {view.action ? (
              <p className="mt-0.5 text-xs break-words text-muted-foreground">{view.action}</p>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function DeepPlan({ deep }: { deep: AskDeep }): JSX.Element {
  return (
    <div className="mt-3 border-t border-border pt-3">
      {deep.banner ? <p className="text-sm font-medium">{deep.banner}</p> : null}
      {deep.planText ? (
        <p className="mt-1 text-xs whitespace-pre-wrap text-muted-foreground">{deep.planText}</p>
      ) : null}
      {deep.cards.length ? (
        <ul className="m-0 mt-2 flex list-none flex-col gap-1.5 p-0">
          {deep.cards.map((card) => (
            <li key={card.question}>
              <details className="rounded-sm border border-border">
                <summary className="flex min-h-9 cursor-pointer items-center gap-3 px-3 py-1.5 text-sm">
                  <StatusBadge
                    tone={card.status === "done" ? "done" : "queued"}
                    label={card.status === "done" ? "Done" : "Waiting"}
                  />
                  <span className="min-w-0 flex-1 break-words">{card.question}</span>
                </summary>
                {card.answer ? (
                  <div className="border-t border-border px-3 py-2 text-sm">
                    <Markdown text={card.answer} />
                  </div>
                ) : (
                  <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
                    No answer yet.
                  </p>
                )}
              </details>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function AskThinking({ turn }: { turn: AskTurn }): JSX.Element {
  const panelId = useId();
  // The reader's own choice wins; until there is one the turn decides — open while it runs, folded when it is done.
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? (turn.streaming || turn.recovered);
  return (
    <div className="rounded-sm border border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setChosen(!open)}
        className="flex min-h-9 w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronDown
          aria-hidden
          className={`size-4 shrink-0 transition-transform ${open ? "" : "-rotate-90"}`}
        />
        {thinkingLabel(turn)}
      </button>
      <div id={panelId} hidden={!open} className="border-t border-border px-3 py-3">
        <Steps stages={turn.stages} />
        {turn.deep ? <DeepPlan deep={turn.deep} /> : null}
      </div>
    </div>
  );
}
