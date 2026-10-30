"use client";

/** The Ask stage: a conversation with the ingested videos. The thread lives in the URL (`?thread=`), so a refresh or a shared
 * link lands on the same conversation; a thread with no turns yet is "New". Reads search params, so it must sit inside a
 * Suspense boundary. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import type { JSX } from "react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/shared/ui/button";

import { branchThread, deleteThread, fetchAskUsage } from "../api";
import { lastSourcedTurn, shortId } from "../ask";
import { type AskSendOptions, useAskConversation } from "../hooks/use-ask-conversation";
import { useMediaQuery } from "../hooks/use-media-query";
import { useYcsOverlays } from "../hooks/use-overlays";
import { useStickToBottom } from "../hooks/use-stick-to-bottom";
import { ASK_EXAMPLES, ASK_THREAD_PARAM, askHref } from "../params";
import { AskComposer } from "./ask-composer";
import { AskSources } from "./ask-sources";
import { THREADS_QUERY_KEY } from "./ask-threads";
import { AskToolbar } from "./ask-toolbar";
import { AskTurnView } from "./ask-turn";
import { PipelineUsageSheet } from "./pipeline-usage-sheet";
import { SideSheet } from "./side-sheet";

export function AskStage(): JSX.Element {
  const router = useRouter();
  const params = useSearchParams();
  const fromUrl = params.get(ASK_THREAD_PARAM) ?? "";
  // A thread with no `?thread=` yet gets a candidate id that stays put until the first message makes it real.
  const [candidate, setCandidate] = useState(() => shortId());
  const threadId = fromUrl || candidate;
  const open = useCallback((id: string) => router.push(askHref(id)), [router]);
  return (
    <AskConversation
      key={threadId}
      threadId={threadId}
      inUrl={Boolean(fromUrl)}
      onOpenThread={open}
      onNewThread={() => {
        setCandidate(shortId());
        router.push(askHref());
      }}
      onPersistUrl={() => router.replace(askHref(threadId))}
    />
  );
}

function AskConversation({
  threadId,
  inUrl,
  onOpenThread,
  onNewThread,
  onPersistUrl,
}: {
  threadId: string;
  inUrl: boolean;
  onOpenThread: (id: string) => void;
  onNewThread: () => void;
  onPersistUrl: () => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const { toast, notice, confirm } = useYcsOverlays();
  const conversation = useAskConversation(threadId);
  const { turns, running, status, hydrated, hydrateError } = conversation;

  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState("");
  const [scope, setScope] = useState<string[]>([]);
  const [sourcesTurn, setSourcesTurn] = useState<number | null>(null);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [sheet, setSheet] = useState<"sources" | "usage" | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wide = useMediaQuery("(min-width: 1024px)");
  const { following, jump } = useStickToBottom(turns);

  // The first message fixes the thread's scope; every later one (and every regenerate) sends it again.
  const locked = turns.length > 0;
  const lockedScope = useMemo(() => turns[0]?.channelIds ?? [], [turns]);
  const options = useCallback(
    (): AskSendOptions => ({ forceMode: mode || undefined, channelIds: locked ? lockedScope : scope }),
    [mode, locked, lockedScope, scope],
  );

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

  const usage = useQuery({
    queryKey: ["ycs", "ask", "usage", threadId],
    queryFn: () => fetchAskUsage(threadId),
    enabled: sheet === "usage",
  });

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
          () => toast("Answer copied."),
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
          tone: "primary",
        });
        if (!yes) return;
        if (!(await latest.current.regenerate(index, latest.current.options()))) {
          notice("Wait for the running answer to finish, or press Stop, before you regenerate.", "info");
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
          tone: "primary",
        });
        if (!yes) return;
        try {
          const branch = await branchThread(threadId, turn.createdAt || null);
          if (!branch) {
            notice("The server did not create a branch.", "error");
            return;
          }
          toast(
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
    [confirm, notice, toast, threadId, onOpenThread, queryClient, fill],
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
  const railTitle = shown ? `Sources for “${shown.question}”` : "Sources";

  return (
    <div className="flex flex-col gap-4">
      <AskToolbar
        mode={mode}
        onMode={setMode}
        scope={locked ? lockedScope : scope}
        scopeLocked={locked}
        onScope={setScope}
        threadId={threadId}
        threadSaved={locked}
        onSwitchThread={(id) => (running ? busyNotice("switch threads") : onOpenThread(id))}
        onDeleteThread={(id) => void onDeleteThread(id)}
        onNewThread={() => (running ? busyNotice("start a new thread") : onNewThread())}
        onUsage={() => setSheet("usage")}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-w-0 flex-col">
          {!hydrated ? <p className="text-sm text-muted-foreground">Loading the conversation…</p> : null}
          {hydrateError ? (
            <p role="alert" className="text-sm text-destructive-text">
              Could not load this thread: {hydrateError}
            </p>
          ) : null}

          {isEmpty ? (
            <div className="py-10">
              <h2 className="m-0 text-lg font-semibold tracking-tight">Ask about your videos</h2>
              <p className="mt-1 mb-4 text-sm text-muted-foreground">
                Answers come from the transcripts you have ingested, with the videos they came from.
              </p>
              <ul
                aria-label="Example questions"
                className="m-0 flex list-none flex-col items-start gap-2 p-0"
              >
                {ASK_EXAMPLES.map((example) => (
                  <li key={example} className="max-w-full">
                    <button
                      type="button"
                      onClick={() => fill(example)}
                      className="min-h-9 max-w-full cursor-pointer rounded-sm border border-border px-3 py-1.5 text-left text-sm break-words hover:border-primary hover:text-primary-text"
                    >
                      {example}
                    </button>
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
            className="flex flex-col gap-8 pb-6"
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

        {wide ? (
          <aside aria-label="Sources" className="min-w-0">
            <div className="sticky top-20 max-h-[calc(100dvh-6rem)] overflow-y-auto pr-1">
              <h2 className="mt-0 mb-2 text-sm font-semibold break-words">{railTitle}</h2>
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
          usage={usage.data ?? null}
          title="Conversation LLM usage"
          subtitle="Every answer in this thread."
          onClose={() => setSheet(null)}
        />
      ) : null}
    </div>
  );
}
