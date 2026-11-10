"use client";

/** The Ask stage: a book-scoped conversation over the Agentic RAG index. The book and thread live in the URL, so a refresh or
 * shared link restores the same context. Reads search params, so it sits inside a Suspense boundary. */

import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import type { JSX } from "react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { LibraryPicker } from "../../components/library-list";
import { useBdOverlays } from "../../hooks/use-overlays";
import { branchThread, deleteThread } from "../api";
import { lastSourcedTurn, shortId } from "../ask";
import { type AskSendOptions, useAskConversation } from "../hooks/use-ask-conversation";
import { useMediaQuery } from "../hooks/use-media-query";
import { useStickToBottom } from "../hooks/use-stick-to-bottom";
import { ASK_EXAMPLES, ASK_THREAD_PARAM, askHref } from "../params";
import { AskComposer } from "./ask-composer";
import { AskSources } from "./ask-sources";
import { THREADS_QUERY_KEY } from "./ask-threads";
import { AskToolbar } from "./ask-toolbar";
import { AskTurnView } from "./ask-turn";
import { PipelineUsageSheet } from "./pipeline-usage-sheet";
import { RagActionButton as Button } from "./rag-action-button";
import { SideSheet } from "./side-sheet";

export function AskStage(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const bookId = params.get("book") ?? "";
  const fromUrl = params.get(ASK_THREAD_PARAM) ?? "";
  // A thread with no `?thread=` yet gets a candidate id that stays put until the first message makes it real.
  const [candidate, setCandidate] = useState(() => shortId());
  const threadId = fromUrl || candidate;
  const open = useCallback((id: string) => router.push(askHref(bookId, id)), [router, bookId]);
  if (!bookId) {
    return (
      <div className="flex min-h-[calc(100dvh-16rem)] flex-col items-start justify-center gap-4">
        <div>
          <h2 className="m-0 text-lg font-semibold tracking-tight">Choose a book to ask</h2>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">
            Ask searches one distilled book at a time and cites the pages used in each answer.
          </p>
        </div>
        <LibraryPicker selected={null} showActions={false} onOpen={(id) => router.push(askHref(id))} />
      </div>
    );
  }
  return (
    <AskConversation
      key={threadId}
      bookId={bookId}
      threadId={threadId}
      inUrl={Boolean(fromUrl)}
      onOpenThread={open}
      onNewThread={() => {
        setCandidate(shortId());
        router.push(askHref(bookId));
      }}
      onPersistUrl={() => router.replace(askHref(bookId, threadId))}
      onOpenBook={(id) => router.push(askHref(id))}
    />
  );
}

function AskConversation({
  bookId,
  threadId,
  inUrl,
  onOpenThread,
  onNewThread,
  onPersistUrl,
  onOpenBook,
}: {
  bookId: string;
  threadId: string;
  inUrl: boolean;
  onOpenThread: (id: string) => void;
  onNewThread: () => void;
  onPersistUrl: () => void;
  onOpenBook: (id: string | null) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const { notice, confirm } = useBdOverlays();
  const conversation = useAskConversation(threadId, bookId);
  const { turns, running, status, hydrated, hydrateError } = conversation;

  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState("");
  const [sourcesTurn, setSourcesTurn] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [sheet, setSheet] = useState<"sources" | "usage" | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wide = useMediaQuery("(min-width: 1024px)");
  const { following, jump } = useStickToBottom(turns);

  const options = useCallback((): AskSendOptions => ({ forceMode: mode || undefined }), [mode]);

  const shownTurn = sourcesTurn ?? lastSourcedTurn(turns);
  const shown = shownTurn === null ? null : (turns[shownTurn] ?? null);

  // The saved-thread list changes when a turn lands; refresh it whenever a run ends.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) void queryClient.invalidateQueries({ queryKey: THREADS_QUERY_KEY });
    wasRunning.current = running;
  }, [running, queryClient]);

  // `/` jumps to the message box unless the reader is already typing somewhere.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      event.preventDefault();
      inputRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // A pill chosen in the text brings its card into view in the rail.
  useEffect(() => {
    if (highlight === null) return;
    document.getElementById(`ask-source-${highlight}`)?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  const busyNotice = (what: string) =>
    notice(`Wait for the answer to finish, or press Stop, before you ${what}.`, "info");

  const send = (question: string) => {
    const text = question.trim();
    if (!text || running) return;
    setDraft("");
    setSourcesTurn(null);
    setHighlight(null);
    if (!inUrl) onPersistUrl();
    void conversation.send(text, options());
  };

  const fill = useCallback((text: string) => {
    setDraft(text);
    inputRef.current?.focus();
  }, []);

  // The handlers below are built once and read the newest turns through this ref, so a finished turn is not re-rendered for
  // every streamed token.
  const latest = useRef({ turns, options, regenerate: conversation.regenerate });
  useLayoutEffect(() => {
    latest.current = { turns, options, regenerate: conversation.regenerate };
  });

  const actions = useMemo(
    () => ({
      onCite: (index: number, n: number) => {
        setSourcesTurn(index);
        setHighlight(n);
        if (!window.matchMedia("(min-width: 1024px)").matches) setSheet("sources");
      },
      onSources: (index: number) => {
        setSourcesTurn(index);
        setHighlight(null);
        if (!window.matchMedia("(min-width: 1024px)").matches) setSheet("sources");
      },
      onCopy: (index: number) => {
        const answer = latest.current.turns[index]?.answer ?? "";
        navigator.clipboard.writeText(answer).then(
          () => notice("Answer copied."),
          () => notice("Copying was blocked by the browser.", "error"),
        );
      },
      onRegenerate: async (index: number) => {
        const turn = latest.current.turns[index];
        if (!turn) return;
        const yes = await confirm({
          title: "Regenerate this answer?",
          message:
            "The current answer is deleted and the question is asked again. To keep it, branch the thread first.",
          confirmLabel: "Regenerate",
        });
        if (!yes) return;
        if (!(await latest.current.regenerate(index, latest.current.options()))) {
          notice("Wait for the running answer to finish, or press Stop, before you regenerate.", "info");
        }
      },
      onRetry: async (index: number) => {
        // No confirmation: a failed turn has no answer to lose.
        if (!(await latest.current.regenerate(index, latest.current.options()))) {
          notice("Wait for the running answer to finish, or press Stop, before you retry.", "info");
        }
      },
      onBranch: async (index: number) => {
        const turn = latest.current.turns[index];
        if (!turn) return;
        const yes = await confirm({
          title: "Branch this conversation?",
          message:
            "Copy the thread up to and including this answer into a new thread and switch to it. The original stays as it is.",
          confirmLabel: "Branch",
        });
        if (!yes) return;
        try {
          const branch = await branchThread(threadId, turn.createdAt || null);
          if (!branch) {
            notice("The server did not create a branch.", "error");
            return;
          }
          notice(
            `Branched into a new thread (${branch.copied} turn${branch.copied === 1 ? "" : "s"} copied).`,
          );
          void queryClient.invalidateQueries({ queryKey: THREADS_QUERY_KEY });
          onOpenThread(branch.newThreadId);
        } catch {
          notice("Branching failed.", "error");
        }
      },
      onFollowup: (question: string) => fill(question),
    }),
    [confirm, notice, threadId, onOpenThread, queryClient, fill],
  );

  const onDeleteThread = async (id: string) => {
    const yes = await confirm({
      title: "Delete this thread?",
      message: "The thread and all of its answers are deleted for good.",
      confirmLabel: "Delete",
    });
    if (!yes) return;
    if (!(await deleteThread(id))) {
      notice("Deleting the thread failed.", "error");
      return;
    }
    void queryClient.invalidateQueries({ queryKey: THREADS_QUERY_KEY });
    if (id === threadId) onNewThread();
  };

  const isEmpty = hydrated && !hydrateError && turns.length === 0;
  const railTitle = shown ? `Sources (${shown.citations.length})` : "Sources";

  return (
    <div className="flex flex-col gap-4">
      <AskToolbar
        mode={mode}
        onMode={setMode}
        bookId={bookId}
        onOpenBook={onOpenBook}
        threadId={threadId}
        threadTitle={turns[0]?.question ?? ""}
        onSwitchThread={(id) => (running ? busyNotice("switch threads") : onOpenThread(id))}
        onDeleteThread={(id) => void onDeleteThread(id)}
        onNewThread={() => (running ? busyNotice("start a new thread") : onNewThread())}
        onUsage={() => setSheet("usage")}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {/* Tall enough that the message box sits at the bottom of the screen even before there is a conversation. */}
        <div className="flex min-h-[calc(100dvh-23rem)] min-w-0 flex-col sm:min-h-[calc(100dvh-18rem)]">
          {!hydrated ? <p className="text-sm text-muted-foreground">Loading the conversation…</p> : null}
          {hydrateError ? (
            <p role="alert" className="text-sm text-destructive-text">
              Could not load this thread: {hydrateError}
            </p>
          ) : null}

          {isEmpty ? (
            <div className="flex-1 py-10">
              <h2 className="m-0 text-lg font-semibold tracking-tight">Ask about this book</h2>
              <p className="mt-1 mb-4 text-sm text-muted-foreground">
                Answers come from indexed book pages and link back to the passages they use.
              </p>
              <ul
                aria-label="Example questions"
                className="m-0 flex list-none flex-col items-start gap-2 p-0"
              >
                {ASK_EXAMPLES.map((example) => (
                  <li key={example} className="max-w-full">
                    <Button
                      type="button"
                      onClick={() => fill(example)}
                      className="h-auto min-h-10 max-w-full whitespace-normal text-left break-words"
                    >
                      {example}
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* One announcement per finished answer: `aria-busy` holds the log's announcements back while a turn streams. */}
          <div
            role="log"
            aria-label="Conversation"
            aria-relevant="additions"
            aria-busy={running}
            className="flex flex-1 flex-col gap-8 pb-6"
          >
            {turns.map((turn, index) => (
              <AskTurnView
                key={turn.key}
                turn={turn}
                index={index}
                sourcesShown={shownTurn === index}
                canBranch={Boolean(turn.createdAt) || index === turns.length - 1}
                busy={running}
                actions={actions}
              />
            ))}
          </div>

          {!following && turns.length > 0 ? (
            <div className="sticky bottom-28 z-10 flex justify-center">
              <Button type="button" variant="outline" size="lg" onClick={jump} className="bg-background">
                <ArrowDown aria-hidden data-icon="inline-start" />
                Jump to latest
              </Button>
            </div>
          ) : null}

          <AskComposer
            value={draft}
            onChange={setDraft}
            onSend={() => send(draft)}
            onStop={() => void conversation.stop()}
            running={running}
            status={status}
            inputRef={inputRef}
          />
        </div>

        {/* Nothing to say until the first question: the rail appears with the conversation, and an empty first screen
            does not carry a "no sources" note. */}
        {wide && turns.length > 0 ? (
          <aside aria-label="Sources" className="min-w-0">
            <div className="sticky top-[calc(var(--chrome-top)+1.5rem)] max-h-[calc(100dvh-var(--chrome-top)-2.5rem)] overflow-y-auto pr-1">
              <h2 className="mt-0 mb-1 text-sm font-semibold">{railTitle}</h2>
              {shown ? (
                <p className="mt-0 mb-2 line-clamp-2 text-xs break-words text-muted-foreground">
                  {shown.question}
                </p>
              ) : null}
              <AskSources citations={shown?.citations ?? []} highlight={highlight} heading={railTitle} />
            </div>
          </aside>
        ) : null}
      </div>

      {sheet === "sources" && !wide ? (
        <SideSheet
          title="Sources"
          subtitle={shown?.question}
          closeLabel="Close sources"
          onClose={() => setSheet(null)}
        >
          <AskSources citations={shown?.citations ?? []} highlight={highlight} heading={railTitle} />
        </SideSheet>
      ) : null}
      {sheet === "usage" ? (
        <PipelineUsageSheet
          threadId={turns.length > 0 ? threadId : null}
          title="Conversation LLM usage"
          subtitle="Every answer in this thread."
          emptyMessage="No conversation yet. Usage appears here after the first answer."
          onClose={() => setSheet(null)}
        />
      ) : null}
    </div>
  );
}
