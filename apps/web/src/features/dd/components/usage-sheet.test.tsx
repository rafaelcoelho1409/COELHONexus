import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { cellsFromIds } from "../domain/chapters";
import type { PlannerRunController } from "../hooks/use-planner-run";
import type { SynthRunController } from "../hooks/use-synth-run";
import { PipelineTotal } from "./pipeline-total";
import { PlanHealth } from "./plan-health";
import { PlannerUsageSheet, SynthUsageSheet } from "./usage-sheet";

const API = "*/api/v1/dd";

const counts = (calls: number, tokensIn: number, tokensOut: number, reasoning = 0) => ({
  calls,
  tokens_in: tokensIn,
  tokens_out: tokensOut,
  reasoning_tokens: reasoning,
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => client.setConfig({ baseUrl: "http://nexus.test" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const renderWith = (children: React.ReactNode) => render(<QueryProvider>{children}</QueryProvider>);

describe("PlannerUsageSheet", () => {
  it("shows calls and tokens, then each busy node, and never a model", async () => {
    server.use(
      http.get(`${API}/planner/debug/graph/*/llm-counters`, () =>
        HttpResponse.json({
          total: counts(14, 9000, 2100),
          by_node: {
            off_topic: { ...counts(12, 6000, 300), by_model: { "p/model-x": counts(12, 6000, 300) } },
            chapter_propose: counts(2, 3000, 1800),
            corpus_load: counts(0, 0, 0),
          },
        }),
      ),
    );
    renderWith(<PlannerUsageSheet threadId="planner/alpha/t1" live={false} onClose={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Planner LLM usage" });
    expect(await within(dialog).findByText("9,000")).toBeInTheDocument();
    const table = within(dialog).getByRole("region", { name: "Usage by node" });
    expect(
      within(table)
        .getAllByRole("row")
        .map((row) => row.textContent),
    ).toEqual([
      "NodeCallsInput tokensOutput tokens",
      "Off-topic filter126,000300",
      "Chapter propose23,0001,800",
    ]);
    expect(dialog.textContent).not.toContain("model-x");
  });

  it("says so when nothing was recorded", async () => {
    server.use(http.get(`${API}/planner/debug/graph/*/llm-counters`, () => HttpResponse.json({})));
    renderWith(<PlannerUsageSheet threadId="planner/alpha/t1" live={false} onClose={vi.fn()} />);
    expect(await screen.findByText("No LLM usage recorded yet.")).toBeInTheDocument();
  });
});

describe("SynthUsageSheet", () => {
  const cells = cellsFromIds(["ch-a", "ch-b"]).map((cell, index) => ({
    ...cell,
    status: "done" as const,
    threadId: `synth/alpha/${index + 1}`,
  }));

  function chapterCounters() {
    server.use(
      http.get(`${API}/synth/debug/graph/*/llm-counters`, ({ request }) => {
        const second = decodeURIComponent(request.url).includes("synth/alpha/2");
        return HttpResponse.json({
          total: second ? counts(3, 1000, 500) : counts(5, 4000, 1000, 200),
          by_node: { sawc_write: second ? counts(3, 1000, 500) : counts(5, 4000, 1000, 200) },
        });
      }),
    );
  }

  it("shows each chapter's usage and, with every chapter rendered, the combined total", async () => {
    chapterCounters();
    renderWith(<SynthUsageSheet cells={cells} live={false} onClose={vi.fn()} />);

    const combined = await screen.findByRole("region", { name: "All chapters combined" });
    // 4,000 + 1,000 input tokens: once in the headline figure, once in the node row
    expect(await within(combined).findAllByText("5,000")).toHaveLength(2);
    expect(within(combined).getByText("reasoning tokens")).toBeInTheDocument();
    expect(screen.getAllByRole("group")).toHaveLength(2); // one <details> per chapter
  });

  it("holds the combined total back while a chapter is still pending", async () => {
    chapterCounters();
    const [first, second] = cells;
    if (!first || !second) throw new Error("fixture");
    renderWith(
      <SynthUsageSheet cells={[first, { ...second, status: "pending" }]} live={false} onClose={vi.fn()} />,
    );

    expect(
      await screen.findByText("The combined total appears after all chapters are done."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "All chapters combined" })).not.toBeInTheDocument();
  });
});

describe("PlanHealth", () => {
  it("names what degraded the plan", async () => {
    server.use(
      http.get(`${API}/planner/alpha/health`, () =>
        HttpResponse.json({
          degraded: true,
          pipeline_health: { chapter_propose_fallback_used: true, chapter_select_n_chapters: 2 },
        }),
      ),
    );
    renderWith(<PlanHealth slug="alpha" />);
    expect(await screen.findByText("degraded")).toBeInTheDocument();
    expect(screen.getByText("Chapter proposals fell back to deterministic titles.")).toBeInTheDocument();
    expect(screen.getByText("Only 2 chapters survived selection.")).toBeInTheDocument();
  });

  it("shows nothing when the server cannot say", async () => {
    server.use(http.get(`${API}/planner/alpha/health`, () => HttpResponse.json({ degraded: null })));
    const { container } = renderWith(<PlanHealth slug="alpha" />);
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("PipelineTotal", () => {
  const planner = (over: Partial<PlannerRunController>): PlannerRunController =>
    ({
      values: { plan_path: "p" },
      running: false,
      threadId: "planner/alpha/t1",
      ...over,
    }) as PlannerRunController;
  const synth = (over: Partial<SynthRunController>): SynthRunController =>
    ({
      cells: [
        { id: "a", title: "A", status: "done", wallMs: null, threadId: "synth/alpha/1", selected: false },
      ],
      running: false,
      ...over,
    }) as SynthRunController;

  function allCounters() {
    server.use(
      http.get(`${API}/planner/debug/graph/*/llm-counters`, () =>
        HttpResponse.json({ total: counts(14, 9000, 2100), by_node: {} }),
      ),
      http.get(`${API}/synth/debug/graph/*/llm-counters`, () =>
        HttpResponse.json({ total: counts(5, 4000, 1000), by_node: {} }),
      ),
    );
  }

  it("adds the planner and every chapter once both are finished", async () => {
    allCounters();
    renderWith(<PipelineTotal planner={planner({})} synth={synth({})} />);
    expect(
      await screen.findByText(/19 calls · 13,000 input tokens · 3,100 output tokens/),
    ).toBeInTheDocument();
  });

  it("shows no moving partial while either side is still running or incomplete", async () => {
    allCounters();
    const { container, rerender } = renderWith(
      <PipelineTotal planner={planner({ running: true })} synth={synth({})} />,
    );
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(container).toBeEmptyDOMElement();

    rerender(
      <QueryProvider>
        <PipelineTotal planner={planner({ values: {} })} synth={synth({})} />
      </QueryProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
