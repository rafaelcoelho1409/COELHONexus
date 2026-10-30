"use client";

/** The assistant that turns a plain-language request into a query. One connection per request, read with the shared
 * SSE reader (a POST stream has no reconnect — a retry would just ask again). Each frame updates the status line, and
 * the query text goes to `onText`: a chunk replaces the draft, and the final cleaned text wins on `done`. */

import { useCallback, useEffect, useRef, useState } from "react";

import { readMessages } from "@/shared/sse";

import { queryAiUrl } from "../api";
import { streamErrorText } from "../ask";
import { queryAiEventSchema } from "../entities";
import type { QueryBackend } from "../params";
import { AI_IDLE, AI_STARTED, type AiState, aiText, applyAiEvent } from "../query";

export type QueryAi = {
  state: AiState;
  running: boolean;
  start: (request: {
    backend: QueryBackend;
    prompt: string;
    /** What is in the editor now, so a follow-up can refine it. */
    previous: string;
    onText: (text: string) => void;
  }) => void;
  stop: () => void;
};

export function useQueryAi(): QueryAi {
  const [state, setState] = useState<AiState>(AI_IDLE);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState((current) =>
      current.phase === "running" || current.phase === "repair"
        ? { phase: "idle", message: "Stopped" }
        : current,
    );
  }, []);

  const start = useCallback<QueryAi["start"]>(({ backend, prompt, previous, onText }) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState(AI_STARTED);
    void (async () => {
      try {
        for await (const message of readMessages({
          url: queryAiUrl(backend),
          method: "POST",
          body: { app: "ycs", prompt, previous },
          signal: controller.signal,
        })) {
          if (!message.data) continue;
          let frame: ReturnType<typeof queryAiEventSchema.parse>;
          try {
            frame = queryAiEventSchema.parse(JSON.parse(message.data));
          } catch {
            continue; // an unparseable frame is skipped, not fatal
          }
          setState((current) => applyAiEvent(current, frame));
          const text = aiText(frame);
          if (text !== null) onText(text);
          if (frame.event === "done" || frame.event === "error") break;
        }
        if (controller.signal.aborted) return;
        // A close with no `done` or `error` frame would leave the status on "Generating…" for good.
        setState((current) =>
          current.phase === "running" || current.phase === "repair"
            ? { phase: "error", message: "The assistant stopped before finishing" }
            : current,
        );
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({ phase: "error", message: streamErrorText(error) });
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    })();
  }, []);

  return { state, running: state.phase === "running" || state.phase === "repair", start, stop };
}
