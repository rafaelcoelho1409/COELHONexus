import { createParser, type EventSourceMessage } from "eventsource-parser";

/** The server answered, but not with a usable event stream (non-2xx status, or the wrong content type). */
export class StreamHttpError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(status: number, message: string, body = "") {
    super(message);
    this.name = "StreamHttpError";
    this.status = status;
    this.body = body;
  }
}

export type StreamRequest = {
  url: string;
  /** SSE over POST is how the Ask endpoints stream; `EventSource` cannot do it, `fetch` can. */
  method?: "GET" | "POST";
  /** JSON-serialised when present. */
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal | undefined;
  /** Sent as `Last-Event-ID` so the server can resume after a drop. */
  lastEventId?: string | undefined;
};

export type ReadHooks = {
  /** The server's `retry:` field — its preferred reconnect delay in milliseconds. */
  onRetry?: (ms: number) => void;
};

/**
 * One connection's worth of raw SSE messages. Ends when the server closes the stream; throws
 * `StreamHttpError` for a non-2xx or non-event-stream answer and the fetch error for a dropped connection.
 * Use `streamEvents` for reconnects, validation and terminal events.
 */
export async function* readMessages(
  request: StreamRequest,
  hooks: ReadHooks = {},
): AsyncGenerator<EventSourceMessage, void, void> {
  const { url, method = "GET", body, headers = {}, signal, lastEventId } = request;
  const response = await fetch(url, {
    method,
    cache: "no-store",
    signal: signal ?? null,
    headers: {
      Accept: "text/event-stream",
      "Cache-Control": "no-cache",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      ...headers,
    },
    body: body === undefined ? null : JSON.stringify(body),
  });

  if (!response.ok) {
    const detail = (await response.text().catch(() => "")).slice(0, 500);
    throw new StreamHttpError(
      response.status,
      `Stream request failed: ${response.status} ${response.statusText}`,
      detail,
    );
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("text/event-stream")) {
    throw new StreamHttpError(
      response.status,
      `Expected an event stream, got "${contentType || "no content type"}"`,
    );
  }
  if (!response.body) {
    throw new StreamHttpError(response.status, "The stream response has no body");
  }

  const pending: EventSourceMessage[] = [];
  const parser = createParser({
    onEvent: (message) => pending.push(message),
    onRetry: (ms) => hooks.onRetry?.(ms),
  });
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      parser.feed(decoder.decode(value, { stream: true }));
      for (const message of pending.splice(0)) yield message;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}
