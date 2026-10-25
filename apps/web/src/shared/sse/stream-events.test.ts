// @vitest-environment node
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";

import { StreamHttpError } from "./read-messages";
import { type StreamOptions, type StreamStatus, streamEvents } from "./stream-events";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const URL = "http://nexus.test/api/events";
const encoder = new TextEncoder();
const eventSchema = z.object({ n: z.number(), done: z.boolean().optional() });
type Evt = z.infer<typeof eventSchema>;

const options = (extra: Partial<StreamOptions<Evt>> = {}): StreamOptions<Evt> => ({
  url: URL,
  parse: (data) => eventSchema.parse(data),
  isTerminal: (event) => event.done === true,
  backoff: { initialMs: 1, maxMs: 2 },
  sleep: async () => undefined,
  ...extra,
});

/**
 * A text/event-stream response made of the given chunks (each chunk is one network read). `error` drops
 * the connection shortly after the chunks were sent. Erroring a ReadableStream discards whatever is still
 * queued, and msw tees (eagerly reads) the body of every mocked response — so, like a real network
 * failure, the drop happens a moment later, after the consumer has read what was delivered.
 */
function sse(chunks: string[], init: { error?: boolean; keepOpen?: boolean } = {}) {
  let delivered = false;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller): Promise<void> | undefined {
      if (!delivered) {
        delivered = true;
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        if (!init.error && !init.keepOpen) controller.close();
        return undefined;
      }
      if (!init.error) return undefined;
      return new Promise<void>((resolve) =>
        setTimeout(() => {
          controller.error(new TypeError("connection dropped"));
          resolve();
        }, 40),
      );
    },
  });
  return new HttpResponse(stream, { headers: { "Content-Type": "text/event-stream" } });
}

async function collect<T>(stream: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of stream) out.push(item);
  return out;
}

describe("streamEvents", () => {
  it("parses events across chunk boundaries, ignores comments and joins multi-line data", async () => {
    server.use(
      http.get(URL, () =>
        sse([
          ": keep-alive\n\n",
          'data: {"n":',
          '1}\n\ndata: {"n":2,\n',
          'data: "x":0}\n\n', // two `data:` lines join with \n into one valid JSON event
          "data: this is not json\n\n", // a malformed event is skipped, not fatal
          'data: {"n":3,"done":true}\n\n',
        ]),
      ),
    );
    const invalid: unknown[] = [];
    const events = await collect(streamEvents(options({ onInvalid: (error) => invalid.push(error) })));
    expect(events).toEqual([{ n: 1 }, { n: 2 }, { n: 3, done: true }]);
    expect(invalid).toHaveLength(1);
  });

  it("sends the JSON body and headers of a POST (how Ask streams)", async () => {
    let seen:
      | { method: string; contentType: string | null; accept: string | null; body: unknown }
      | undefined;
    server.use(
      http.post(URL, async ({ request }) => {
        seen = {
          method: request.method,
          contentType: request.headers.get("content-type"),
          accept: request.headers.get("accept"),
          body: await request.json(),
        };
        return sse(['data: {"n":1,"done":true}\n\n']);
      }),
    );
    await collect(
      streamEvents(options({ method: "POST", body: { question: "why?" }, headers: { "X-Test": "1" } })),
    );
    expect(seen).toEqual({
      method: "POST",
      contentType: "application/json",
      accept: "text/event-stream",
      body: { question: "why?" },
    });
  });

  it("stops at the terminal event even though the server keeps the connection open", async () => {
    server.use(
      http.get(URL, () => sse(['data: {"n":1}\n\ndata: {"n":2,"done":true}\n\n'], { keepOpen: true })),
    );
    const statuses: StreamStatus[] = [];
    const events = await collect(streamEvents(options({ onStatus: (s) => statuses.push(s) })));
    expect(events.map((e) => e.n)).toEqual([1, 2]);
    expect(statuses).toEqual(["connecting", "open", "closed"]);
  });

  it("without a terminal rule, a clean server close ends the stream", async () => {
    server.use(http.get(URL, () => sse(['data: {"n":1}\n\n'])));
    const events = await collect(streamEvents(options({ isTerminal: undefined })));
    expect(events).toEqual([{ n: 1 }]);
  });

  it("reconnects after a dropped connection and resumes with Last-Event-ID", async () => {
    const lastEventIds: (string | null)[] = [];
    server.use(
      http.get(URL, ({ request }) => {
        lastEventIds.push(request.headers.get("last-event-id"));
        if (lastEventIds.length === 1) return sse(['id: 7\ndata: {"n":1}\n\n'], { error: true });
        return sse(['id: 8\ndata: {"n":2,"done":true}\n\n']);
      }),
    );
    const statuses: StreamStatus[] = [];
    const events = await collect(streamEvents(options({ onStatus: (s) => statuses.push(s) })));
    expect(events.map((e) => e.n)).toEqual([1, 2]);
    expect(lastEventIds).toEqual([null, "7"]);
    expect(statuses).toEqual(["connecting", "open", "reconnecting", "open", "closed"]);
  });

  it("retries a 503 and then succeeds", async () => {
    let calls = 0;
    server.use(
      http.get(URL, () => {
        calls += 1;
        return calls < 3 ? new HttpResponse("busy", { status: 503 }) : sse(['data: {"n":1,"done":true}\n\n']);
      }),
    );
    expect(await collect(streamEvents(options()))).toEqual([{ n: 1, done: true }]);
    expect(calls).toBe(3);
  });

  it("does not retry a 4xx — the request itself is wrong", async () => {
    let calls = 0;
    server.use(
      http.get(URL, () => {
        calls += 1;
        return new HttpResponse("no such run", { status: 404, statusText: "Not Found" });
      }),
    );
    const error = await collect(streamEvents(options())).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(StreamHttpError);
    expect(error).toMatchObject({ status: 404, body: "no such run" });
    expect(calls).toBe(1);
  });

  it("rejects a 200 that is not an event stream", async () => {
    server.use(http.get(URL, () => HttpResponse.json({ not: "a stream" })));
    const error = await collect(streamEvents(options())).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(StreamHttpError);
    expect((error as Error).message).toContain("Expected an event stream");
  });

  it("gives up after maxRetries consecutive failures and surfaces the last error", async () => {
    let calls = 0;
    server.use(
      http.get(URL, () => {
        calls += 1;
        return new HttpResponse("down", { status: 502 });
      }),
    );
    const error = await collect(streamEvents(options({ maxRetries: 2 }))).catch((caught: unknown) => caught);
    expect(error).toMatchObject({ status: 502 });
    expect(calls).toBe(3); // the first attempt + 2 retries
  });

  it("ends quietly when the caller aborts", async () => {
    server.use(http.get(URL, () => sse(['data: {"n":1}\n\n'], { keepOpen: true })));
    const controller = new AbortController();
    const statuses: StreamStatus[] = [];
    const seen: Evt[] = [];
    for await (const event of streamEvents(
      options({ signal: controller.signal, onStatus: (s) => statuses.push(s) }),
    )) {
      seen.push(event);
      controller.abort();
    }
    expect(seen).toEqual([{ n: 1 }]);
    expect(statuses.at(-1)).toBe("closed");
  });
});
