import type { EventSourceMessage } from "eventsource-parser";

import { readMessages, StreamHttpError, type StreamRequest } from "./read-messages";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed";

export type StreamOptions<T> = StreamRequest & {
  /** Turns the JSON-decoded `data` of one message into a typed event; throw to reject it (e.g. a zod parse). */
  parse: (data: unknown, message: EventSourceMessage) => T;
  /** The event after which the stream is over. Without it, a clean server close ends the stream. */
  isTerminal?: (event: T) => boolean;
  /** Consecutive failed connections before giving up. Default 8. */
  maxRetries?: number;
  /** Reconnect delay: exponential from `initialMs`, capped at `maxMs`; a server `retry:` field wins. */
  backoff?: { initialMs: number; maxMs: number };
  onStatus?: (status: StreamStatus) => void;
  /** An event that fails `parse` is skipped, not fatal; this is told about it. */
  onInvalid?: (error: unknown, message: EventSourceMessage) => void;
  /** Injected for tests. */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
};

const DEFAULT_BACKOFF = { initialMs: 500, maxMs: 15_000 };
const DEFAULT_MAX_RETRIES = 8;

function defaultSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    }
    signal?.addEventListener("abort", done, { once: true });
  });
}

/** 4xx means the request itself is wrong, so retrying cannot help — except "timeout" and "slow down". */
function isPermanent(error: unknown): boolean {
  return (
    error instanceof StreamHttpError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 408 &&
    error.status !== 429
  );
}

/**
 * Typed, resilient server-sent events: one async iterator over every event of a logical stream, across
 * reconnects. A dropped connection or a 5xx reconnects with backoff and `Last-Event-ID`; a 4xx throws at
 * once; aborting the signal ends the iterator quietly; a terminal event ends it after yielding it.
 */
export async function* streamEvents<T>(options: StreamOptions<T>): AsyncGenerator<T, void, void> {
  const { parse, isTerminal, onStatus, onInvalid, signal } = options;
  const maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
  const backoff = options.backoff ?? DEFAULT_BACKOFF;
  const sleep = options.sleep ?? defaultSleep;
  let lastEventId = options.lastEventId;
  let serverRetryMs: number | undefined;
  let failures = 0;
  let attempt = 0;

  for (;;) {
    onStatus?.(attempt === 0 ? "connecting" : "reconnecting");
    attempt += 1;
    try {
      let opened = false;
      for await (const message of readMessages(
        { ...options, lastEventId },
        { onRetry: (ms) => (serverRetryMs = ms) },
      )) {
        if (!opened) {
          opened = true;
          failures = 0;
          onStatus?.("open");
        }
        if (message.id) lastEventId = message.id;
        let event: T;
        try {
          event = parse(JSON.parse(message.data), message);
        } catch (error) {
          onInvalid?.(error, message);
          continue;
        }
        yield event;
        if (isTerminal?.(event)) {
          onStatus?.("closed");
          return;
        }
      }
      // The server closed cleanly. Without a terminal-event rule that means "finished".
      if (!isTerminal) {
        onStatus?.("closed");
        return;
      }
    } catch (error) {
      if (signal?.aborted) {
        onStatus?.("closed");
        return;
      }
      if (isPermanent(error)) throw error;
      failures += 1;
      if (failures > maxRetries) throw error;
    }
    if (signal?.aborted) {
      onStatus?.("closed");
      return;
    }
    const exponential = Math.min(backoff.maxMs, backoff.initialMs * 2 ** Math.max(failures - 1, 0));
    await sleep(serverRetryMs ?? exponential, signal);
  }
}
