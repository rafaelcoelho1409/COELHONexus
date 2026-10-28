"use client";

/** Ask conversation controller for one thread. Mounted with `key={threadId}` — a different thread is a
 * fresh controller that hydrates that thread's history. Owns the turn list, the live SSE turn and the
 * refresh-recovery poll. The SSE reader is the one-shot `readMessages`: a POST stream must never
 * reconnect (a retry would re-run the question), so a drop or a clean close just ends the turn. */

import { useCallback, useEffect, useRef, useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { readMessages } from "@/shared/sse";

import {
  applyAskEvent,
  type AskStatus,
  type AskTurn,
  isTurnInProgress,
  newLiveTurn,
  snapshotKey,
  statusFromEvent,
  streamErrorText,
  turnFromHistory,
} from "../ask";
import { askStreamUrl, cancelAskTurn, fetchHistory } from "../api";
import type { AskEvent } from "../entities";
import { askEventSchema } from "../entities";
import { HISTORY_POLL_MS, STALL_TICKS_THRESHOLD } from "../params";

export type AskSendOptions = {
  forceMode?: string;
  channelIds: readonly string[];
  subQuestions?: readonly string[];
};

export type AskConversation = {
  turns: AskTurn[];
  /** Something is consuming a slot: a live SSE turn in this tab, or a recovered in-progress turn. */
  running: boolean;
  /** The refresh-recovery poll is following an in-progress turn restored from history. */
  recovering: boolean;
  status: AskStatus;
  hydrateError: string | null;
  send: (question: string, options: AskSendOptions) => Promise<void>;
  stop: () => Promise<void>;
  /** Deletes the turn's row (best-effort), drops it from the list, then re-fires the question.
   * Returns false when blocked by a running turn (the stage shows the toast). */
  regenerate: (index: number, options: AskSendOptions) => Promise<boolean>;
};

type StreamJob = {
  payload: {
    question: string;
    thread_id: string;
    channel_ids?: string[];
    force_mode?: string;
    sub_questions?: string[];
  };
};

export function useAskConversation(threadId: string): AskConversation {
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const [stream, setStream] = useState<StreamJob | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [status, setStatus] = useState<AskStatus>({ text: "", tone: "" });
  const [hydrateError, setHydrateError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const running = stream !== null || recovering;

  // Hydrate: the persisted turns of this thread, oldest first; a mid-stream snapshot starts recovery.
  useEffect(() => {
    let alive = true;
    void (async () => {
      setTurns([]);
      setStatus({ text: "", tone: "" });
      setHydrateError(null);
      let rows;
      try {
        rows = await fetchHistory(threadId);
      } catch (error) {
        if (!alive) return;
        setHydrateError(apiErrorMessage(error));
        return;
      }
      if (!alive) return;
      const hydrated = rows.map(turnFromHistory);
      setTurns(hydrated);
      const last = rows.at(-1);
      if (last && isTurnInProgress(last.thinking_state, last.answer)) {
        setRecovering(true);
        setStatus({ text: "Thinking…", tone: "running" });
      }
    })();
    return () => {
      alive = false;
    };
  }, [threadId]);

  const apply = useCallback((event: AskEvent) => {
    setTurns((ts) => {
      const last = ts.at(-1);
      if (!last || !last.streaming) return ts;
      return [...ts.slice(0, -1), applyAskEvent(last, event)];
    });
    setStatus((prev) => statusFromEvent(prev, event));
  }, []);

  // One live SSE connection per send. A POST retry would re-run the question, so no reconnect: a drop
  // or a clean close just ends the turn (the close without `end` freezes it as-is).
  useEffect(() => {
    if (!stream) return;
    const controller = new AbortController();
    abortRef.current = controller;
    let alive = true;
    void (async () => {
      try {
        for await (const message of readMessages({
          url: askStreamUrl(),
          method: "POST",
          body: stream.payload,
          signal: controller.signal,
        })) {
          if (!alive) return;
          if (!message.data) continue;
          let event;
          try {
            event = askEventSchema.parse(JSON.parse(message.data));
          } catch {
            continue; // an unparseable frame is skipped, not fatal
          }
          apply(event);
          if (event.node === "end") break;
        }
        if (!alive) return;
        setTurns((ts) => ts.map((turn) => (turn.streaming ? { ...turn, streaming: false } : turn)));
      } catch (error) {
        if (!alive || controller.signal.aborted) return;
        const text = streamErrorText(error);
        setTurns((ts) =>
          ts.map((turn, i) =>
            i === ts.length - 1 && turn.streaming ? { ...turn, streaming: false, error: text } : turn,
          ),
        );
        setStatus({ text: `Failed: ${text}`, tone: "error" });
      } finally {
        abortRef.current = null;
      }
      if (alive) setStream(null);
    })();
    return () => {
      alive = false;
      abortRef.current = null;
      controller.abort();
    };
  }, [stream, apply]);

  // Recovery poll: the backend persists snapshots while the (now detached) SSE generator keeps running.
  useEffect(() => {
    if (!recovering) return;
    let alive = true;
    let lastKey = "";
    let ticks = 0;
    const poll = async () => {
      let rows;
      try {
        rows = await fetchHistory(threadId);
      } catch {
        return; // transient — keep polling
      }
      if (!alive) return;
      const last = rows.at(-1);
      if (!last) {
        setRecovering(false);
        return;
      }
      const key = snapshotKey(last);
      ticks = key && key === lastKey ? ticks + 1 : 0;
      lastKey = key;
      setTurns(rows.map(turnFromHistory));
      if (ticks >= STALL_TICKS_THRESHOLD) {
        setTurns((ts) =>
          ts.map((turn) => (turn.recovered ? { ...turn, recovered: false, stalled: true } : turn)),
        );
        setRecovering(false);
        setStatus({ text: "", tone: "" });
        return;
      }
      if (!isTurnInProgress(last.thinking_state, last.answer)) {
        setRecovering(false);
        setStatus({ text: "", tone: "" });
      }
    };
    const handle = setInterval(() => void poll(), HISTORY_POLL_MS);
    return () => {
      alive = false;
      clearInterval(handle);
    };
  }, [recovering, threadId]);

  const send = useCallback(
    async (question: string, options: AskSendOptions) => {
      if (!question || running) return;
      setRecovering(false); // a live stream owns the conversation from here on
      setHydrateError(null);
      setStatus({ text: "Thinking…", tone: "running" });
      setTurns((ts) => [...ts, newLiveTurn(question)]);
      const payload: StreamJob["payload"] = { question, thread_id: threadId };
      if (options.channelIds.length) payload.channel_ids = [...options.channelIds];
      if (options.forceMode) payload.force_mode = options.forceMode;
      if (options.subQuestions?.length) payload.sub_questions = [...options.subQuestions];
      setStream({ payload });
    },
    [running, threadId],
  );

  const stop = useCallback(async () => {
    const inFlight = turns.find((turn) => turn.streaming);
    abortRef.current?.abort();
    abortRef.current = null;
    if (inFlight?.turnId != null) await cancelAskTurn(inFlight.turnId);
    if (recovering && inFlight?.turnId != null) await cancelAskTurn(inFlight.turnId);
    setTurns((ts) => ts.filter((turn) => !turn.streaming));
    setStream(null);
    setRecovering(false);
    setStatus({ text: "", tone: "" });
  }, [recovering, turns]);

  const regenerate = useCallback(
    async (index: number, options: AskSendOptions) => {
      if (running) return false;
      const turn = turns[index];
      if (!turn) return false;
      if (turn.turnId != null) void cancelAskTurn(turn.turnId);
      setTurns((ts) => ts.filter((_, i) => i !== index));
      await send(turn.question, options);
      return true;
    },
    [running, turns, send],
  );

  return {
    turns,
    running,
    recovering,
    status,
    hydrateError,
    send,
    stop,
    regenerate,
  };
}
