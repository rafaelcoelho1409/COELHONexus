"use client";

import { useEffect, useRef, useState } from "react";

import { type StreamOptions, type StreamStatus, streamEvents } from "./stream-events";

/**
 * Subscribes to a stream for as long as the component is mounted. Pass `null` to stay disconnected.
 * `options` must be referentially stable (memoise it, or let the React Compiler do it): a new object
 * restarts the stream. Events go to `onEvent`; keep accumulated state in the caller (or the query cache).
 */
export function useEventStream<T>(options: StreamOptions<T> | null, onEvent: (event: T) => void) {
  const [status, setStatus] = useState<StreamStatus>("closed");
  const [error, setError] = useState<unknown>(null);
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    onEventRef.current = onEvent;
  });

  useEffect(() => {
    if (options === null) return; // an earlier stream's abort already reported "closed"
    const controller = new AbortController();
    (async () => {
      try {
        for await (const event of streamEvents({
          ...options,
          signal: controller.signal,
          onStatus: (next) => {
            setStatus(next);
            if (next === "connecting") setError(null); // a fresh start forgets the previous failure
            options.onStatus?.(next);
          },
        })) {
          onEventRef.current(event);
        }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught);
      }
    })();
    return () => controller.abort();
  }, [options]);

  return { status, error };
}
