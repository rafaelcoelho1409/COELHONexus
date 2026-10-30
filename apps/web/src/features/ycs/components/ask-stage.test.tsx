import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";
import { AskStage } from "./ask-stage";
import { YcsOverlaysProvider } from "./overlays";

const ORIGIN = "http://nexus.test";
const API = "*/api";

const nav = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  params: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: nav.push,
    replace: nav.replace,
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
  }),
  usePathname: () => "/ycs/ask",
  useSearchParams: () => nav.params,
  useSelectedLayoutSegment: () => null,
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  nav.push.mockClear();
  nav.replace.mockClear();
  nav.params = new URLSearchParams();
  client.setConfig({ baseUrl: ORIGIN });
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  // jsdom has no layout, so no scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn();
  server.use(
    http.get(`${API}/v1/ycs/admin/ingested-channels`, () =>
      HttpResponse.json({
        items: [
          { channel_id: "c1", channel: "Backend Weekly", video_count: 12 },
          { channel_id: "c2", channel: "Data Hour", video_count: 4 },
        ],
      }),
    ),
    http.get(`${API}/v1/ycs/agents/history/:threadId`, () => HttpResponse.json({ items: [] })),
  );
});
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

function renderStage() {
  return render(
    <QueryProvider>
      <YcsOverlaysProvider>
        <AskStage />
      </YcsOverlaysProvider>
    </QueryProvider>,
  );
}

function sse(events: unknown[]): HttpResponse<string> {
  const body = `${events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("")}\n\n`;
  return new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });
}

const SOURCE = {
  title: "Redis in practice",
  url: "https://youtube.test/watch?v=vid1aaaaaaa",
  video_id: "vid1aaaaaaa",
  channel: "Backend Weekly",
  snippet:
    "Redis keeps its data in memory and writes snapshots to disk, so a restart can load the last snapshot back quickly.",
  source: "qdrant_hybrid",
};

const ANSWER_EVENTS = [
  { node: "_meta", turn_id: 7 },
  { node: "classify_query", mode: "standard" },
  { node: "retrieve", documents: [], document_count: 3 },
  { node: "grade_documents" },
  {
    node: "generate",
    generation: "Redis is an in-memory store. [Video: Redis in practice]",
    citations: [SOURCE],
  },
  { node: "end", duration_ms: 9000, usage: { total: { calls: 2, tokens_in: 1200, tokens_out: 300 } } },
];

function historyRow(partial: Record<string, unknown> = {}) {
  return {
    id: 5,
    question: "What is Redis?",
    answer: "Redis is a store. [Video: Redis in practice]",
    mode: "standard",
    created_at: "2026-10-29T10:00:00Z",
    thinking_state: { channel_ids: ["c1"], duration_ms: 4000, citations: [SOURCE] },
    ...partial,
  };
}

describe("Ask stage — new thread", () => {
  it("offers example questions that fill the box, and Send waits for text", async () => {
    const user = userEvent.setup();
    renderStage();
    expect(await screen.findByRole("heading", { name: "Ask about your videos" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send/ })).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Compare the recommendations made by each channel." }),
    );
    expect(screen.getByRole("textbox", { name: "Ask about your videos" })).toHaveValue(
      "Compare the recommendations made by each channel.",
    );
    expect(screen.getByRole("button", { name: /Send/ })).toBeEnabled();
  });

  it("streams an answer: pills, folded trace, timing, tokens and a source sheet", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post(`${API}/v1/ycs/agents/search/stream`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return sse(ANSWER_EVENTS);
      }),
    );
    const user = userEvent.setup();
    renderStage();
    await screen.findByRole("heading", { name: "Ask about your videos" });

    await user.click(screen.getByLabelText("Deep"));
    await user.type(screen.getByRole("textbox", { name: "Ask about your videos" }), "What is Redis?{Enter}");

    expect(await screen.findByText(/Redis is an in-memory store/)).toBeInTheDocument();
    expect(body).toMatchObject({ question: "What is Redis?", force_mode: "deep" });
    expect(body.channel_ids).toBeUndefined();
    expect(typeof body.thread_id).toBe("string");
    expect(nav.replace).toHaveBeenCalledWith(`/ycs/ask?thread=${String(body.thread_id)}`);

    expect(await screen.findByText("Answered in 9.0s")).toBeInTheDocument();
    expect(screen.getByText("1,200 in · 300 out")).toBeInTheDocument();
    // done → the trace is folded to one line
    expect(screen.getByRole("button", { name: "Thought for 9.0s" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    await user.click(screen.getByRole("button", { name: "Source 1: Redis in practice" }));
    const sheet = await screen.findByRole("dialog");
    const card = within(sheet).getByRole("link", { name: /Redis in practice/ });
    expect(card).toHaveAttribute("href", SOURCE.url);
    expect(within(sheet).getByText("vector")).toBeInTheDocument();
    expect(within(sheet).getByText("Backend Weekly")).toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "More" }));
    expect(within(sheet).getByRole("button", { name: "Less" })).toBeInTheDocument();
  });

  it("shows a failed stream as an error on the turn", async () => {
    server.use(
      http.post(`${API}/v1/ycs/agents/search/stream`, () =>
        HttpResponse.json({ detail: "no models" }, { status: 503 }),
      ),
    );
    const user = userEvent.setup();
    renderStage();
    await user.type(await screen.findByRole("textbox", { name: "Ask about your videos" }), "Hello?{Enter}");
    expect(await screen.findByText("no models")).toBeInTheDocument();
    expect(screen.getAllByRole("alert").some((el) => /Failed/.test(el.textContent ?? ""))).toBe(true);
  });

  it("shows the server's error text as a failed turn with a Retry, not as an answer", async () => {
    const asked: unknown[] = [];
    server.use(
      http.post(`${API}/v1/ycs/agents/search/stream`, async ({ request }) => {
        asked.push(await request.json());
        return asked.length === 1
          ? sse([
              { node: "_meta", turn_id: 7 },
              { node: "retrieve", documents: [], document_count: 3 },
              {
                node: "generate",
                generation: "Error generating answer: Connection error.",
                citations: [SOURCE],
              },
              { node: "end", duration_ms: 20_000 },
            ])
          : sse(ANSWER_EVENTS);
      }),
    );
    const user = userEvent.setup();
    renderStage();
    await user.type(
      await screen.findByRole("textbox", { name: "Ask about your videos" }),
      "What is Redis?{Enter}",
    );

    const band = await screen.findByRole("alert");
    expect(band).toHaveTextContent("The answer could not be generated.");
    expect(band).toHaveTextContent("Connection error.");
    // The error text is not rendered as an answer, and the turn offers no actions that need an answer.
    expect(screen.queryByText(/Error generating answer/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Sources/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Branch" })).not.toBeInTheDocument();
    expect(screen.getByText("Ended in 20.0s")).toBeInTheDocument();
    // The status line says Failed to the end of the run — never "Done.".
    expect(screen.getByText("Failed: Connection error.")).toBeInTheDocument();
    expect(screen.queryByText("Done.")).not.toBeInTheDocument();

    // Retry asks the same question again at once, with no confirmation.
    await user.click(within(band).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Redis is an in-memory store/)).toBeInTheDocument();
    expect(asked).toHaveLength(2);
    expect(asked[1]).toMatchObject({ question: "What is Redis?" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a saved turn whose answer is the server's error as failed, too", async () => {
    nav.params = new URLSearchParams("thread=t1");
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () =>
        HttpResponse.json({ items: [historyRow({ answer: "Error generating answer: model is down" })] }),
      ),
    );
    renderStage();
    const band = await screen.findByRole("alert");
    expect(band).toHaveTextContent("model is down");
    expect(within(band).getByRole("button", { name: "Retry" })).toBeEnabled();
    expect(screen.queryByText(/Error generating answer/)).not.toBeInTheDocument();
  });

  it("names the thread by its first question", async () => {
    nav.params = new URLSearchParams("thread=t1");
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () =>
        HttpResponse.json({
          items: [historyRow({ question: "What are the main themes across all videos?" })],
        }),
      ),
    );
    renderStage();
    expect(
      await screen.findByRole("button", { name: "Thread: What are the main themes acr…" }),
    ).toBeInTheDocument();
  });

  it("has no sources note on the empty first screen", async () => {
    renderStage();
    await screen.findByRole("heading", { name: "Ask about your videos" });
    expect(screen.getByRole("button", { name: "Thread: New" })).toBeInTheDocument();
    expect(screen.queryByText(/no sources/i)).not.toBeInTheDocument();
  });

  it("keeps Shift+Enter as a new line", async () => {
    const user = userEvent.setup();
    renderStage();
    const box = await screen.findByRole("textbox", { name: "Ask about your videos" });
    await user.type(box, "one{Shift>}{Enter}{/Shift}two");
    expect(box).toHaveValue("one\ntwo");
  });

  it("lets the reader pick channels before the first message", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post(`${API}/v1/ycs/agents/search/stream`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return sse(ANSWER_EVENTS);
      }),
    );
    const user = userEvent.setup();
    renderStage();
    await user.click(await screen.findByRole("button", { name: /Channels: All channels/ }));
    await user.click(await screen.findByRole("checkbox", { name: /Data Hour/ }));
    expect(screen.getByRole("button", { name: /Channels: Data Hour/ })).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Ask about your videos" }), "Anything?{Enter}");
    await screen.findByText(/Redis is an in-memory store/);
    expect(body.channel_ids).toEqual(["c2"]);
    // after the first message the scope is fixed
    expect(screen.queryByRole("button", { name: /Channels:/ })).not.toBeInTheDocument();
    expect(screen.getByText(/locked to this thread/)).toBeInTheDocument();
  });
});

describe("Ask stage — saved thread", () => {
  beforeEach(() => {
    nav.params = new URLSearchParams({ thread: "t1" });
  });

  it("restores the turns and locks the scope the thread began with", async () => {
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () => HttpResponse.json({ items: [historyRow()] })),
    );
    renderStage();
    expect(await screen.findByText("What is Redis?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source 1: Redis in practice" })).toBeInTheDocument();
    expect(screen.getByText(/Backend Weekly/)).toBeInTheDocument();
    expect(screen.getByText(/locked to this thread/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sources \(1\)/ })).toBeEnabled();
    // a thread that exists is not offered the examples
    expect(screen.queryByRole("heading", { name: "Ask about your videos" })).not.toBeInTheDocument();
  });

  it("picks a run that was mid-way up again and offers Stop", async () => {
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () =>
        HttpResponse.json({
          items: [
            historyRow({
              answer: "",
              thinking_state: { stages: { retrieve: { status: "active", action: "Searching transcripts" } } },
            }),
          ],
        }),
      ),
    );
    renderStage();
    expect(await screen.findByText("Picking up where the page left off…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Thinking" })).toHaveAttribute("aria-expanded", "true");
  });

  it("copies an answer", async () => {
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () => HttpResponse.json({ items: [historyRow()] })),
    );
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    renderStage();
    await screen.findByText("What is Redis?");
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(write).toHaveBeenCalledWith("Redis is a store. [Video: Redis in practice]");
    expect(await screen.findByText("Answer copied.")).toBeInTheDocument();
  });

  it("regenerates after a confirmation: deletes the old turn, then asks again", async () => {
    const cancelled: string[] = [];
    let asked: Record<string, unknown> = {};
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () => HttpResponse.json({ items: [historyRow()] })),
      http.post(`${API}/v1/ycs/agents/turns/:turnId/cancel`, ({ params }) => {
        cancelled.push(String(params.turnId));
        return HttpResponse.json({ ok: true });
      }),
      http.post(`${API}/v1/ycs/agents/search/stream`, async ({ request }) => {
        asked = (await request.json()) as Record<string, unknown>;
        return sse(ANSWER_EVENTS);
      }),
    );
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("What is Redis?");
    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Regenerate" }));
    await screen.findByText(/Redis is an in-memory store/);
    expect(cancelled).toEqual(["5"]);
    // the thread's scope is sent again, not the (unlocked) selection
    expect(asked).toMatchObject({ question: "What is Redis?", thread_id: "t1", channel_ids: ["c1"] });
    expect(screen.getAllByTestId("ask-turn")).toHaveLength(1);
  });

  it("branches after a confirmation and switches to the new thread", async () => {
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () => HttpResponse.json({ items: [historyRow()] })),
      http.post(`${API}/v1/ycs/agents/threads/t1/branch`, async ({ request }) => {
        expect(await request.json()).toEqual({ up_to_created_at: "2026-10-29T10:00:00Z" });
        return HttpResponse.json({ new_thread_id: "t9", copied: 1 });
      }),
    );
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("What is Redis?");
    await user.click(screen.getByRole("button", { name: "Branch" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Branch" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/ycs/ask?thread=t9"));
    expect(await screen.findByText("Branched into a new thread (1 turn copied).")).toBeInTheDocument();
  });

  it("lists the saved threads, switches to one and deletes one", async () => {
    const deleted: string[] = [];
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () => HttpResponse.json({ items: [historyRow()] })),
      http.get(`${API}/v1/ycs/agents/threads`, () =>
        HttpResponse.json({
          items: [
            {
              thread_id: "t1",
              first_question: "What is Redis?",
              turn_count: 1,
              last_seen: new Date(Date.now() - 2 * 3_600_000).toISOString(),
            },
            {
              thread_id: "t2",
              first_question: "How do indexes work?",
              turn_count: 3,
              // Relative to now: the picker prints "1d ago", so a fixed date would go stale the next day.
              last_seen: new Date(Date.now() - 36 * 3_600_000).toISOString(),
            },
          ],
        }),
      ),
      http.delete(`${API}/v1/ycs/agents/threads/:threadId`, ({ params }) => {
        deleted.push(String(params.threadId));
        return HttpResponse.json({ ok: true });
      }),
    );
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("What is Redis?");
    await user.click(screen.getByRole("button", { name: /Thread:/ }));
    expect(await screen.findByText("3 turns · 1d ago")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete thread: How do indexes work?" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleted).toEqual(["t2"]));

    await user.click(screen.getByRole("button", { name: /^How do indexes work\?/ }));
    expect(nav.push).toHaveBeenCalledWith("/ycs/ask?thread=t2");
  });

  it("opens the conversation's LLM usage", async () => {
    server.use(
      http.get(`${API}/v1/ycs/agents/history/t1`, () => HttpResponse.json({ items: [historyRow()] })),
      http.get(`${API}/v1/ycs/agents/usage/t1`, () =>
        HttpResponse.json({
          total: { calls: 4, tokens_in: 2000, tokens_out: 500, reasoning_tokens: 0 },
          by_node: {
            generate: { by_model: { "vendor-a/model-x": { calls: 4, tokens_in: 2000, tokens_out: 500 } } },
          },
        }),
      ),
    );
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("What is Redis?");
    await user.click(screen.getByRole("button", { name: "LLM usage" }));
    const sheet = await screen.findByRole("dialog");
    // the total tile and the model's row
    expect(await within(sheet).findAllByText("2,000")).toHaveLength(2);
    expect(within(sheet).getByText("vendor-a/model-x")).toBeInTheDocument();
    expect(within(sheet).getByRole("heading", { name: "Conversation LLM usage" })).toBeInTheDocument();
  });
});
