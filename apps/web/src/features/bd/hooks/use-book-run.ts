"use client";

/** Book Distiller — follows one book's extraction. The snapshot (`GET /bd/ingestion/{id}`) says whether a run is live;
 * while one is, this connects to its event stream, which replays the whole history first and then goes live, so a
 * reload, a reconnect or a second tab all end up with the same picture. Accumulated state lives in a reducer here
 * ("keep accumulated state in the caller", `shared/sse`). Mount it with `key={bookId}`. */

import { useEffect, useMemo, useReducer } from "react";

import type { Snapshot } from "@/shared/api";
import { type StreamStatus, useEventStream } from "@/shared/sse";

import { eventsUrl } from "../api";
import { type BookStatus, isLive, toBookStatus } from "../domain/books";
import { EMPTY_RUN, effectiveStatus, pagesPerMinute, type RunState, runReducer } from "../domain/run-state";
import { bdEventSchema, isTerminalEvent } from "../entities";
import { CANCEL_SAFETY_MS } from "../params";
import { useRefreshBook } from "./use-bd-data";

export type BookRunController = {
  state: RunState;
  status: BookStatus;
  /** A run is live on the server and its terminal event has not arrived. */
  streaming: boolean;
  streamStatus: StreamStatus;
  pagesPerMinute: number | null;
  error: string | null;
  /** A new run was requested: forget the last run's ending so its stream can connect. */
  restart: () => void;
  /** Cancel was requested: say so until the run really stops (or the safety time passes). */
  markCanceling: () => void;
};

const parseEvent = (data: unknown) => bdEventSchema.parse(data);

export function useBookRun(bookId: string, snapshot: Snapshot): BookRunController {
  const refresh = useRefreshBook();
  const [state, dispatch] = useReducer(runReducer, EMPTY_RUN);

  const serverLive =
    isLive(toBookStatus(snapshot.manifest.status)) || isLive(toBookStatus(snapshot.progress?.status));
  const streaming = serverLive && state.terminal === null;

  const options = useMemo(
    () => (streaming ? { url: eventsUrl(bookId), parse: parseEvent, isTerminal: isTerminalEvent } : null),
    [streaming, bookId],
  );

  const { status: streamStatus } = useEventStream(options, (event) => {
    dispatch({ type: "event", event });
    if (isTerminalEvent(event)) refresh(bookId); // the library row and the snapshot now have their final status
  });

  useEffect(() => {
    if (!state.canceling) return;
    const timer = setTimeout(() => dispatch({ type: "cancel-timeout" }), CANCEL_SAFETY_MS);
    return () => clearTimeout(timer);
  }, [state.canceling]);

  const status = effectiveStatus(snapshot.manifest.status, snapshot.progress?.status, state.terminal);

  return {
    state,
    status,
    streaming,
    streamStatus,
    pagesPerMinute: pagesPerMinute(state.slices),
    error: status === "failed" ? (state.terminal?.error ?? snapshot.manifest.error) : null,
    restart: () => dispatch({ type: "restart" }),
    markCanceling: () => dispatch({ type: "canceling" }),
  };
}
