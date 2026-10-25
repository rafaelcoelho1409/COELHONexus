import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { PLANNER_FINISHED_STATE, PLANNER_IMPLEMENTED, PLANNER_THREAD_ID } from "../test-fixtures";
import { DdOverlaysProvider } from "./overlays";
import { PipelineView } from "./pipeline-view";
import { PlannerView } from "./planner-view";
import { StudyView } from "./study-view";
import { SynthView } from "./synth-view";

const ORIGIN = "http://nexus.test";
const API = "*/api";

const navParams = { current: new URLSearchParams("slug=alpha") };

vi.mock("next/navigation", async () => {
  const push = vi.fn();
  const replace = vi.fn();
  return {
    useRouter: () => ({ push, replace, refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
    usePathname: () => "/dd",
    useSearchParams: () => navParams.current,
    useSelectedLayoutSegment: () => null,
  };
});

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <a href={href}> {children}</a>
  ),
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  localStorage.clear();
  client.setConfig({ baseUrl: ORIGIN });
  navParams.current = new URLSearchParams("slug=alpha");
  server.use(
    http.get(`${API}/v1/dd/pipeline/active`, () => HttpResponse.json({ planner: null, synth: null })),
    http.get(`${API}/v1/dd/pipeline/:slug/state`, () =>
      HttpResponse.json({ ingestion: true, planner: true, synth: false, study: false }),
    ),
    http.get(`${API}/v1/dd/planner/:slug/health`, () =>
      HttpResponse.json({ slug: "alpha", degraded: false, pipeline_health: {} }),
    ),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWith(children: React.ReactNode) {
  return render(
    <QueryProvider>
      <DdOverlaysProvider>{children}</DdOverlaysProvider>
    </QueryProvider>,
  );
}

function sse(events: unknown[]): HttpResponse<string> {
  const body = `${events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("")}\n\n`;
  return new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });
}

describe("Planner stage", () => {
  it("recovers the last committed plan and offers Resume", async () => {
    server.use(
      http.get(`${API}/v1/dd/planner/info`, () => HttpResponse.json(PLANNER_IMPLEMENTED)),
      http.get(`${API}/v1/dd/planner/alpha/active`, () => HttpResponse.json({ active: false })),
      http.get(`${API}/v1/dd/planner/alpha/timing`, () => HttpResponse.json({ total_wall_ms: 4200 })),
      http.get(`${API}/v1/dd/planner/recent`, () =>
        HttpResponse.json({ recent: [{ slug: "alpha", thread_id: PLANNER_THREAD_ID }] }),
      ),
      // thread ids contain slashes; the generated client sends them percent-encoded
      http.get(`${API}/v1/dd/planner/debug/graph/*/state`, () => HttpResponse.json(PLANNER_FINISHED_STATE)),
    );
    renderWith(<PlannerView slug="alpha" />);
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
    // every node reads as done and carries its headline number
    await waitFor(() => expect(screen.getByText("12 files")).toBeInTheDocument());
    expect(screen.getByText("10 docs scored")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Stage nodes" })).getAllByText(/— done/)).toHaveLength(8);
  });

  it("streams a run to done, then lands on the final plan", async () => {
    server.use(
      http.get(`${API}/v1/dd/planner/info`, () => HttpResponse.json(PLANNER_IMPLEMENTED)),
      http.get(`${API}/v1/dd/planner/alpha/active`, () => HttpResponse.json({ active: false })),
      http.get(`${API}/v1/dd/planner/alpha/timing`, () => HttpResponse.json({ total_wall_ms: 0 })),
      http.get(`${API}/v1/dd/planner/recent`, () => HttpResponse.json({ recent: [] })),
      http.post(`${API}/v1/dd/planner/alpha`, () =>
        HttpResponse.json({ status: "queued", thread_id: "planner/alpha/t1" }),
      ),
      http.get(`${API}/v1/dd/planner/debug/graph/*/state`, () => HttpResponse.json(PLANNER_FINISHED_STATE)),
      http.get(`${API}/v1/dd/planner/*/events`, () =>
        sse([
          { step: "corpus_load", kind: "start" },
          { step: "plan_write", kind: "done" },
          { step: "planner", kind: "terminal", status: "done", total_wall_ms: 4100 },
        ]),
      ),
    );
    renderWith(<PlannerView slug="alpha" />);
    const start = await screen.findByRole("button", { name: "Start" });
    expect(start).toBeInTheDocument();
    await userEvent.click(start);
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(stop).not.toBeDisabled();
    await waitFor(() => expect(screen.getByText("12 files")).toBeInTheDocument(), { timeout: 8_000 });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeDisabled(), {
      timeout: 8_000,
    });
  });
});

describe("Synth stage", () => {
  it("lists planned chapters with counters and run affordances", async () => {
    server.use(
      http.get(`${API}/v1/dd/synth/info`, () =>
        HttpResponse.json({
          implemented: ["outline", "digest", "write", "derive", "checklist", "replan", "render"],
        }),
      ),
      http.get(`${API}/v1/dd/synth/alpha/study/chapters`, () =>
        HttpResponse.json({
          chapters: [
            {
              id: "01_hall",
              title: "Hallucinations",
              order: 1,
              rendered: true,
              wall_ms: 2000,
              thread_id: "synth/alpha/1",
            },
            {
              id: "02_eval",
              title: "Evaluations",
              order: 2,
              rendered: false,
              wall_ms: null,
              thread_id: null,
            },
          ],
          study_total_wall_ms: 4000,
        }),
      ),
      http.get(`${API}/v1/dd/synth/alpha/active`, () => HttpResponse.json({ active: false })),
    );
    renderWith(<SynthView slug="alpha" />);
    expect(await screen.findByTestId("chstrip-counter")).toHaveTextContent("1 / 2");
    expect(screen.getByText("Hallucinations")).toBeInTheDocument();
    expect(screen.getByTestId("chapter-run-01_hall")).not.toBeDisabled();
  });

  it("runs a single chapter to done from the strip play button", async () => {
    server.use(
      http.get(`${API}/v1/dd/synth/info`, () => HttpResponse.json({ implemented: ["write"] })),
      http.get(`${API}/v1/dd/synth/alpha/study/chapters`, () =>
        HttpResponse.json({
          chapters: [
            {
              id: "01_hall",
              title: "Hallucinations",
              order: 1,
              rendered: false,
              wall_ms: null,
              thread_id: null,
            },
          ],
          study_total_wall_ms: 0,
        }),
      ),
      http.get(`${API}/v1/dd/synth/alpha/active`, () => HttpResponse.json({ active: false })),
      http.post(`${API}/v1/dd/synth/alpha`, ({ request }) => {
        expect(request.url).toContain("chapter_id=01_hall");
        return HttpResponse.json({ status: "queued", thread_id: "synth/alpha/9" });
      }),
      http.get(`${API}/v1/dd/synth/*/events`, () =>
        sse([{ step: "synth", kind: "terminal", status: "done", wall_ms: 3000 }]),
      ),
      http.get(`${API}/v1/dd/synth/debug/graph/*/state`, () => HttpResponse.json({ values: {}, next: [] })),
    );
    renderWith(<SynthView slug="alpha" />);
    const play = await screen.findByTestId("chapter-run-01_hall");
    await userEvent.click(play);
    await waitFor(
      () => {
        expect(screen.queryByRole("button", { name: "Stop" })).toBeDisabled();
        expect(screen.getByTestId("chapter-run-01_hall")).toBeEnabled();
      },
      { timeout: 8_000 },
    );
  });
});

describe("Study stage", () => {
  it("renders a synthesized chapter with its heading outline", async () => {
    navParams.current = new URLSearchParams("slug=alpha&chapter=01_hall");
    server.use(
      http.get(`${API}/v1/dd/synth/alpha/study/chapters`, () =>
        HttpResponse.json({
          chapters: [
            {
              id: "01_hall",
              title: "Hallucinations",
              order: 1,
              rendered: true,
              wall_ms: 2000,
              thread_id: "synth/alpha/1",
            },
          ],
          study_total_wall_ms: 2000,
        }),
      ),
      http.get(`${API}/v1/dd/synth/alpha/study/01_hall/artifact/README.md`, () =>
        HttpResponse.text(
          `# Hallucinations\n\nVerifiers cut hallucination rates before collapse.\n\n- input: digest\n- output: chapter\n`,
        ),
      ),
    );
    renderWith(<StudyView slug="alpha" />);
    expect(await screen.findByTestId("study-title")).toHaveTextContent("Hallucinations");
    await waitFor(() =>
      expect(screen.getByText("Verifiers cut hallucination rates before collapse.")).toBeInTheDocument(),
    );
  });
});

describe("Pipeline stage", () => {
  it("disables both start buttons when the other stage is running", async () => {
    server.use(
      http.get(`${API}/v1/dd/planner/info`, () => HttpResponse.json(PLANNER_IMPLEMENTED)),
      http.get(`${API}/v1/dd/planner/alpha/active`, () => HttpResponse.json({ active: false })),
      http.get(`${API}/v1/dd/planner/alpha/timing`, () => HttpResponse.json({ total_wall_ms: 0 })),
      http.get(`${API}/v1/dd/planner/recent`, () => HttpResponse.json({ recent: [] })),
      http.get(`${API}/v1/dd/synth/info`, () => HttpResponse.json({ implemented: ["write"] })),
      http.get(`${API}/v1/dd/synth/alpha/study/chapters`, () =>
        HttpResponse.json({ chapters: [], study_total_wall_ms: 0 }),
      ),
      http.get(`${API}/v1/dd/synth/alpha/active`, () => HttpResponse.json({ active: false })),
      http.get(`${API}/v1/dd/pipeline/active`, () =>
        HttpResponse.json({ planner: null, synth: { slug: "alpha", thread_id: "synth/alpha/z" } }),
      ),
    );
    renderWith(<PipelineView slug="alpha" />);
    const starts = await screen.findAllByRole("button", { name: "Start" });
    expect(starts).toHaveLength(2);
    await waitFor(() => {
      for (const btn of starts) {
        expect(btn).toBeDisabled();
      }
    });
  });

  function mockIdlePipeline() {
    server.use(
      http.get(`${API}/v1/dd/planner/info`, () => HttpResponse.json(PLANNER_IMPLEMENTED)),
      http.get(`${API}/v1/dd/planner/alpha/active`, () => HttpResponse.json({ active: false })),
      http.get(`${API}/v1/dd/planner/alpha/timing`, () => HttpResponse.json({ total_wall_ms: 0 })),
      http.get(`${API}/v1/dd/planner/recent`, () => HttpResponse.json({ recent: [] })),
      http.get(`${API}/v1/dd/planner/debug/graph/*/state`, () => HttpResponse.json({ values: {}, next: [] })),
      http.post(`${API}/v1/dd/planner/alpha`, () =>
        HttpResponse.json({ status: "queued", thread_id: "planner/alpha/t1" }),
      ),
      http.get(`${API}/v1/dd/planner/*/events`, () =>
        sse([{ step: "planner", kind: "terminal", status: "done", total_wall_ms: 10 }]),
      ),
      http.get(`${API}/v1/dd/synth/info`, () => HttpResponse.json({ implemented: ["write"] })),
      http.get(`${API}/v1/dd/synth/alpha/study/chapters`, () =>
        HttpResponse.json({ chapters: [], study_total_wall_ms: 0 }),
      ),
      http.get(`${API}/v1/dd/synth/alpha/active`, () => HttpResponse.json({ active: false })),
    );
  }

  it("starts synth by itself when Auto-chain is on and the planner finishes cleanly", async () => {
    mockIdlePipeline();
    const synthStarts: string[] = [];
    server.use(
      http.post(`${API}/v1/dd/synth/alpha`, ({ request }) => {
        synthStarts.push(request.url);
        return HttpResponse.json({ status: "queued", study_thread_id: "synth/alpha/study", chapter_ids: [] });
      }),
      http.get(`${API}/v1/dd/synth/*/events`, () => sse([])),
    );
    renderWith(<PipelineView slug="alpha" />);

    await userEvent.click(await screen.findByLabelText("Auto-chain"));
    expect(localStorage.getItem("dd:pipeline:autochain")).toBe("1");
    await userEvent.click(await screen.findByTestId("planner-start"));

    await waitFor(() => expect(synthStarts).toHaveLength(1), { timeout: 8_000 });
    expect(synthStarts[0]).toContain("mode=quality");
  });

  it("leaves synth alone when Auto-chain is off", async () => {
    mockIdlePipeline();
    const synthStarts: string[] = [];
    server.use(
      http.post(`${API}/v1/dd/synth/alpha`, ({ request }) => {
        synthStarts.push(request.url);
        return HttpResponse.json({ status: "queued", study_thread_id: "synth/alpha/study", chapter_ids: [] });
      }),
    );
    renderWith(<PipelineView slug="alpha" />);

    await userEvent.click(await screen.findByTestId("planner-start"));
    await waitFor(() => expect(screen.getAllByRole("status")[0]).toHaveTextContent("Done"), {
      timeout: 8_000,
    });
    await new Promise((resolve) => setTimeout(resolve, 600)); // longer than one chain poll
    expect(synthStarts).toEqual([]);
  });
});

describe("Stopping a run", () => {
  it("asks the server to cancel, and the terminal event frees the controls again", async () => {
    let push: (event: unknown) => void = () => {};
    const cancels: string[] = [];
    server.use(
      http.get(`${API}/v1/dd/planner/info`, () => HttpResponse.json(PLANNER_IMPLEMENTED)),
      http.get(`${API}/v1/dd/planner/alpha/active`, () => HttpResponse.json({ active: false })),
      http.get(`${API}/v1/dd/planner/alpha/timing`, () => HttpResponse.json({ total_wall_ms: 0 })),
      http.get(`${API}/v1/dd/planner/recent`, () => HttpResponse.json({ recent: [] })),
      http.get(`${API}/v1/dd/planner/debug/graph/*/state`, () => HttpResponse.json({ values: {}, next: [] })),
      http.post(`${API}/v1/dd/planner/alpha`, () =>
        HttpResponse.json({ status: "queued", thread_id: "planner/alpha/t1" }),
      ),
      http.post(`${API}/v1/dd/planner/*/cancel`, ({ request }) => {
        cancels.push(request.url);
        return HttpResponse.json({ status: "cancel_requested" });
      }),
      http.get(`${API}/v1/dd/planner/*/events`, () => {
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            push = (event) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
          },
        });
        return new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });
      }),
    );
    renderWith(<PlannerView slug="alpha" />);

    await userEvent.click(await screen.findByTestId("planner-start"));
    const stop = await screen.findByRole("button", { name: "Stop" });
    await waitFor(() => expect(stop).toBeEnabled());
    await userEvent.click(stop);

    await waitFor(() => expect(cancels).toHaveLength(1));
    expect(cancels[0]).toContain("docs-distiller%2Falpha%2F"); // the thread id travels as one path segment
    expect(await screen.findByRole("button", { name: "Stopping…" })).toBeDisabled();

    push({ step: "planner", kind: "terminal", status: "cancelled" });

    await waitFor(() => expect(screen.getByTestId("planner-start")).toBeEnabled());
    expect(screen.getByRole("status")).toHaveTextContent("Cancelled");
    expect(screen.getByTestId("planner-stop")).toBeDisabled();
  });
});

describe("Re-running a synthesized chapter", () => {
  const RENDERED = {
    chapters: [
      {
        id: "01_hall",
        title: "Hallucinations",
        order: 1,
        rendered: true,
        wall_ms: 2000,
        thread_id: "synth/alpha/1",
      },
    ],
    study_total_wall_ms: 2000,
  };

  it("asks first, and leaves everything alone when declined", async () => {
    const calls: string[] = [];
    server.use(
      http.get(`${API}/v1/dd/synth/info`, () => HttpResponse.json({ implemented: ["write"] })),
      http.get(`${API}/v1/dd/synth/alpha/study/chapters`, () => HttpResponse.json(RENDERED)),
      http.get(`${API}/v1/dd/synth/alpha/active`, () => HttpResponse.json({ active: false })),
      http.delete(`${API}/v1/dd/synth/alpha/wipe`, ({ request }) => {
        calls.push(`DELETE ${request.url}`);
        return HttpResponse.json({ ok: true });
      }),
      http.post(`${API}/v1/dd/synth/alpha`, ({ request }) => {
        calls.push(`POST ${request.url}`);
        return HttpResponse.json({ status: "queued", thread_id: "synth/alpha/9" });
      }),
    );
    renderWith(<SynthView slug="alpha" />);

    await userEvent.click(await screen.findByTestId("chapter-run-01_hall"));
    const dialog = await screen.findByRole("dialog", { name: "Re-synthesize this chapter?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls).toEqual([]);
    expect(screen.getByTestId("chapter-run-01_hall")).toBeEnabled();
  });
});
