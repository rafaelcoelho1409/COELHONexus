"use client";

/** One question and its answer: the question, the Thinking trace, the answer in markdown with `[n]` source pills, the
 * follow-up questions the model suggested, and the turn's actions. */

import { BookOpen, Copy, GitBranch, RefreshCw } from "lucide-react";
import type { ComponentProps, JSX } from "react";
import { createContext, memo, useContext } from "react";

import { Markdown } from "@/shared/markdown";
import { Button } from "@/shared/ui/button";

import {
  type AskTurn,
  citeMarkdown,
  citeNumber,
  failedAnswerReason,
  formatTurnDuration,
  STALLED_SUBTITLE,
  turnFailure,
  usageLabel,
} from "../ask";
import type { AskCitation } from "../entities";
import { AskThinking } from "./ask-thinking";
import { StatusBadge } from "./status-badge";

type CiteContextValue = { citations: readonly AskCitation[]; onCite: (n: number) => void };
const CiteContext = createContext<CiteContextValue>({ citations: [], onCite: () => {} });

/** A `[n](#cite-n)` link is a source pill; every other link is a plain external link. */
function AnswerLink({
  href,
  children,
  node,
  ...rest
}: ComponentProps<"a"> & { node?: unknown }): JSX.Element {
  void node;
  const { citations, onCite } = useContext(CiteContext);
  const n = citeNumber(href);
  if (n !== null) {
    const title = citations[n - 1]?.title;
    return (
      <button
        type="button"
        onClick={() => onCite(n)}
        aria-label={title ? `Source ${n}: ${title}` : `Source ${n}`}
        className="tabular mx-0.5 inline-flex min-h-6 min-w-6 cursor-pointer items-center justify-center rounded-sm border border-border bg-muted px-1 align-baseline text-xs font-medium text-foreground no-underline hover:border-primary hover:text-primary-text"
      >
        {n}
      </button>
    );
  }
  const external = href !== undefined && /^https?:/i.test(href);
  return (
    <a href={href} {...rest} {...(external ? { target: "_blank", rel: "noreferrer noopener" } : {})}>
      {children}
    </a>
  );
}

const ANSWER_COMPONENTS = { a: AnswerLink };

function Skeleton(): JSX.Element {
  return (
    <div aria-hidden className="flex flex-col gap-2 py-1">
      <div className="animate-status h-3 w-11/12 rounded-sm bg-muted" />
      <div className="animate-status h-3 w-4/5 rounded-sm bg-muted" />
      <div className="animate-status h-3 w-3/5 rounded-sm bg-muted" />
    </div>
  );
}

/** Handlers take the turn's index, so one stable set serves every turn and a finished turn is not re-rendered per token. */
export type AskTurnActions = {
  onCite: (index: number, n: number) => void;
  onSources: (index: number) => void;
  onCopy: (index: number) => void;
  onRegenerate: (index: number) => void;
  /** Ask a failed turn's question again — no confirmation, there is no answer to lose. */
  onRetry: (index: number) => void;
  onBranch: (index: number) => void;
  onFollowup: (question: string) => void;
};

function AskTurnViewBase({
  turn,
  index,
  sourcesShown,
  canBranch,
  busy,
  actions,
}: {
  turn: AskTurn;
  index: number;
  /** This turn's sources are the ones on display. */
  sourcesShown: boolean;
  canBranch: boolean;
  /** Another turn is running: actions that would start work wait. */
  busy: boolean;
  actions: AskTurnActions;
}): JSX.Element {
  const took = formatTurnDuration(turn.durationMs);
  const tokens = usageLabel(turn.usage);
  const live = turn.streaming || turn.recovered;
  // A failed answer is not shown as an answer: the server's error text stands in for it, so it becomes an error band
  // with the reason and a Retry once the turn has stopped.
  const failure = live ? null : turnFailure(turn);
  const answerIsError = failedAnswerReason(turn.answer) !== null;
  const waitingForText = live && (!turn.answer || answerIsError);
  const text = citeMarkdown(turn.answer, turn.citations);
  return (
    <article className="flex flex-col gap-3" data-testid="ask-turn">
      <h3 className="m-0 rounded-sm bg-muted px-3 py-2 text-sm font-medium break-words whitespace-pre-wrap">
        <span className="sr-only">You asked: </span>
        {turn.question}
      </h3>

      <div className="flex flex-col gap-3 pl-0 sm:pl-4">
        {turn.mode || took || tokens ? (
          <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {turn.mode ? (
              <span className="rounded-sm border border-border px-1.5 py-px font-medium capitalize">
                {turn.mode}
              </span>
            ) : null}
            {took ? <span>{failure ? `Ended ${took}` : `Answered ${took}`}</span> : null}
            {tokens ? <span className="tabular">{tokens}</span> : null}
          </p>
        ) : null}

        {turn.hasThinking ? <AskThinking turn={turn} /> : null}

        {waitingForText ? (
          <Skeleton />
        ) : turn.answer && !answerIsError ? (
          <div className="ask-answer text-sm leading-relaxed">
            <CiteContext.Provider
              value={{ citations: turn.citations, onCite: (n) => actions.onCite(index, n) }}
            >
              <Markdown text={text} components={ANSWER_COMPONENTS} />
            </CiteContext.Provider>
          </div>
        ) : null}

        {turn.recovered ? <StatusBadge tone="running" label="Picking up where the page left off…" /> : null}
        {turn.stalled ? (
          <p role="alert" className="m-0 flex flex-wrap items-center gap-x-2 text-sm">
            <StatusBadge tone="failed" label={STALLED_SUBTITLE} />
            <span className="text-muted-foreground">Regenerate to ask again.</span>
          </p>
        ) : null}
        {failure ? (
          <div
            role="alert"
            className="flex flex-col items-start gap-2 rounded-sm border border-destructive p-3"
          >
            <p className="m-0 flex flex-wrap items-center gap-x-2 text-sm">
              <StatusBadge tone="failed" label="Failed" />
              <span className="font-medium">The answer could not be generated.</span>
            </p>
            <p className="m-0 min-w-0 text-sm break-words text-muted-foreground">{failure}</p>
            <Button variant="outline" size="lg" onClick={() => actions.onRetry(index)} disabled={busy}>
              <RefreshCw aria-hidden data-icon="inline-start" />
              Retry
            </Button>
          </div>
        ) : null}

        {!live && turn.followups.length ? (
          <ul aria-label="Suggested follow-ups" className="m-0 flex list-none flex-wrap gap-2 p-0">
            {turn.followups.map((question) => (
              <li key={question} className="max-w-full">
                <button
                  type="button"
                  onClick={() => actions.onFollowup(question)}
                  className="min-h-9 max-w-full cursor-pointer rounded-sm border border-border px-3 py-1.5 text-left text-sm break-words hover:border-primary hover:text-primary-text"
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        {!live && !failure ? (
          <div className="-ml-2 flex flex-wrap items-center gap-1">
            <Button
              variant="ghost"
              size="lg"
              onClick={() => actions.onSources(index)}
              disabled={!turn.citations.length}
              aria-pressed={sourcesShown}
            >
              <BookOpen aria-hidden data-icon="inline-start" />
              Sources ({turn.citations.length})
            </Button>
            <Button variant="ghost" size="lg" onClick={() => actions.onCopy(index)} disabled={!turn.answer}>
              <Copy aria-hidden data-icon="inline-start" />
              Copy
            </Button>
            <Button variant="ghost" size="lg" onClick={() => actions.onRegenerate(index)} disabled={busy}>
              <RefreshCw aria-hidden data-icon="inline-start" />
              Regenerate
            </Button>
            <Button
              variant="ghost"
              size="lg"
              onClick={() => actions.onBranch(index)}
              disabled={busy || !canBranch}
            >
              <GitBranch aria-hidden data-icon="inline-start" />
              Branch
            </Button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export const AskTurnView = memo(AskTurnViewBase);
