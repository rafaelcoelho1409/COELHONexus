import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";
import type { PipelineTracking } from "../hooks/use-pipeline-tracking";
import { YcsOverlaysProvider } from "./overlays";
import { PipelinePanel } from "./pipeline-panel";

const ORIGIN = "http://nexus.test";
const API = "*/api";

const nav = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: nav.push,
    replace: nav.replace,
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
  }),
  usePathname: () => "/ycs/ingestion",
  useSearchParams: () => new URLSearchParams(),
  useSelectedLayoutSegment: () => null,
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  localStorage.clear();
  nav.push.mockClear();
  nav.replace.mockClear();
  client.setConfig({ baseUrl: ORIGIN });
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWith(ui: React.ReactNode) {
  return render(
    <QueryProvider>
      <YcsOverlaysProvider>{ui}</YcsOverlaysProvider>
    </QueryProvider>,
  );
}

const TRACKING: PipelineTracking = {
  extract: "e1",
  qdrant: "e1",
  neo4j: "e1",
  videoIds: ["vid1aaaaaaa", "vid2aaaaaaa"],
  startedAtMs: Date.now() - 30_000,
};

/** Mutable task/stream state each test sets before rendering; every poll re-reads it. */
const taskState: { state: string; meta: Record<string, unknown>; result: Record<string, unknown> | null } = {
  state: "RUNNING",
  meta: {},
  result: null,
};

const streamState: Record<
  "qdrant" | "neo4j",
  { state: string; meta: Record<string, unknown>; result: Record<string, unknown> | null }
> = {
  qdrant: { state: "PENDING", meta: {}, result: null },
  neo4j: { state: "PENDING", meta: {}, result: null },
};

function installHandlers() {
  server.use(
    http.get(`${API}/v1/ycs/admin/task/:taskId`, () => HttpResponse.json({ task_id: "e1", ...taskState })),
    http.get(`${API}/v1/ycs/admin/pipeline/:extractId/stream/:phase`, ({ params }) => {
      const phase = String(params.phase);
      const snap = streamState[phase as "qdrant" | "neo4j"] ?? { state: "PENDING", meta: {} };
      return HttpResponse.json({ task_id: "e1", ...snap });
    }),
    http.get(`${API}/v1/ycs/admin/pipeline/:extractId/llm-counters`, () =>
      HttpResponse.json({ total: { calls: 2, tokens_in: 100, tokens_out: 20, reasoning_tokens: 0 } }),
    ),
    http.post(`${API}/v1/ycs/content/videos/pipeline/:extractId/stop`, () =>
      HttpResponse.json({ status: "stopped" }),
    ),
    http.post(`${API}/v1/ycs/content/videos/pipeline/:extractId/wipe`, () =>
      HttpResponse.json({
        status: "wiped",
        summary: {
          es: { metadata_deleted: 2, transcripts_deleted: 5 },
          qdrant: { qdrant_deleted: 12 },
          neo4j: { documents_deleted: 30, videos_deleted: 2, entities_swept: 8 },
        },
      }),
    ),
    http.post(`${API}/v1/ycs/content/videos/pipeline/:extractId/rerun`, () =>
      HttpResponse.json({
        status: "queued",
        phases: { extract: "e2", qdrant: "e2", neo4j: "e2" },
        video_ids: TRACKING.videoIds,
      }),
    ),
  );
}

describe("PipelinePanel", () => {
  beforeEach(installHandlers);

  it("renders the four bars with legacy titles and the per-video table", async () => {
    taskState.state = "RUNNING";
    taskState.meta = {
      phase: "transcription",
      current: 3,
      total: 10,
      all_items: [
        { id: "vid1aaaaaaa", title: "One", channel: "Chan" },
        { id: "vid2aaaaaaa", title: "Two", channel: "Chan" },
      ],
      current_item: { id: "vid1aaaaaaa", title: "One" },
      completed_ids: ["vid1aaaaaaa"],
      no_transcript_ids: [],
      failed_ids: [],
    };
    streamState.qdrant = { state: "PROGRESS", meta: { completed_ids: ["vid1aaaaaaa"] }, result: null };
    streamState.neo4j = { state: "PENDING", meta: {}, result: null };

    renderWith(
      <PipelinePanel tracking={TRACKING} onPhaseDone={vi.fn()} onAllDone={vi.fn()} onDismiss={vi.fn()} />,
    );

    expect(await screen.findByText("Phase 1 · Playwright")).toBeInTheDocument();
    expect(screen.getByText("Phase 2 · ElasticSearch")).toBeInTheDocument();
    expect(screen.getByText("Phase 3 · Qdrant")).toBeInTheDocument();
    expect(screen.getByText("Phase 4 · Neo4j")).toBeInTheDocument();
    expect(await screen.findByText("3/10")).toBeInTheDocument(); // playwright bar label
    expect(screen.getByText("· 2 videos")).toBeInTheDocument();

    // Each phase says where it stands in an icon and a word, and draws a named progress bar with a value.
    const steps = screen.getByRole("list", { name: "Pipeline phases" });
    expect(within(steps).getAllByRole("progressbar")).toHaveLength(4);
    const playwright = within(steps).getByRole("progressbar", { name: "Phase 1 · Playwright" });
    expect(playwright).toHaveAttribute("aria-valuenow");

    // The per-video table is in a side sheet, failed videos first.
    await userEvent.click(screen.getByRole("button", { name: "Videos (2)" }));
    const sheet = await screen.findByRole("dialog", { name: "Videos in this pipeline" });
    const table = within(sheet).getByRole("region", { name: "Per-video pipeline progress" });
    const row1 = within(table).getByRole("row", { name: /One/ });
    expect(within(row1).getAllByText("Done").length).toBeGreaterThan(0); // qdrant done
    const row2 = within(table).getByRole("row", { name: /Two/ });
    expect(within(row2).getAllByText("Queued").length).toBe(4); // PW, ES, qdrant, neo4j
  });

  it("announces the run once, by words, and keeps the phases themselves out of any live region", async () => {
    taskState.state = "RUNNING";
    taskState.meta = { phase: "transcription", current: 3, total: 10 };
    renderWith(
      <PipelinePanel tracking={TRACKING} onPhaseDone={vi.fn()} onAllDone={vi.fn()} onDismiss={vi.fn()} />,
    );
    await screen.findByText("3/10");
    const live = screen.getAllByRole("status").find((element) => element.className.includes("sr-only"));
    expect(live).toHaveTextContent("Pipeline running.");
    expect(live).toHaveTextContent("Phase 1 · Playwright: running");
    expect(live).not.toHaveTextContent("3/10"); // counts change at every poll; they are not announced
    expect(screen.getByRole("region", { name: "Pipeline progress" })).not.toHaveAttribute("aria-live");
  });

  it("opens the LLM usage in a sheet, with the models the run called", async () => {
    server.use(
      http.get(`${API}/v1/ycs/admin/pipeline/:extractId/llm-counters`, () =>
        HttpResponse.json({
          total: { calls: 3, tokens_in: 1200, tokens_out: 90, reasoning_tokens: 0 },
          by_node: {
            vid1aaaaaaa: {
              by_model: {
                "vendor-a/model-x": { calls: 2, tokens_in: 800, tokens_out: 60, reasoning_tokens: 0 },
              },
            },
            vid2aaaaaaa: {
              by_model: {
                "vendor-b/model-y": { calls: 1, tokens_in: 400, tokens_out: 30, reasoning_tokens: 0 },
              },
            },
          },
        }),
      ),
    );
    renderWith(
      <PipelinePanel tracking={TRACKING} onPhaseDone={vi.fn()} onAllDone={vi.fn()} onDismiss={vi.fn()} />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "LLM usage" }));
    const sheet = await screen.findByRole("dialog", { name: "Pipeline LLM usage" });
    expect(await within(sheet).findByText("1,200")).toBeInTheDocument();
    const models = within(sheet).getByRole("region", { name: "Usage by model" });
    expect(
      within(models)
        .getAllByRole("row")
        .map((row) => row.textContent),
    ).toEqual([
      "ModelCallsInput tokensOutput tokensReasoning",
      "vendor-a/model-x2800600",
      "vendor-b/model-y1400300",
    ]);
  });

  it("shows usage counts and phase hints on success, firing done callbacks once", async () => {
    taskState.state = "SUCCESS";
    taskState.result = {
      total_videos: 2,
      metadata: { indexed: 2 },
      transcriptions: { indexed: 2, cached: 0, fetch_failed: 0, failed: 0, no_transcript: 0 },
      total_transcripts: 2,
      total_chunks: 20,
      points_upserted: 20,
      nodes_created: 5,
      relationships_created: 3,
      entities_merged: 1,
      completed_ids: TRACKING.videoIds,
      failed_ids: [],
      no_transcript_ids: [],
    };
    streamState.qdrant = {
      state: "SUCCESS",
      meta: { completed_ids: TRACKING.videoIds },
      result: { total_transcripts: 2, total_chunks: 20, points_upserted: 20, failed_ids: [] },
    };
    streamState.neo4j = {
      state: "SUCCESS",
      meta: { completed_ids: TRACKING.videoIds },
      result: {
        nodes_created: 5,
        relationships_created: 3,
        entities_merged: 1,
        failed_ids: [],
        completed_ids: [],
      },
    };
    const onPhaseDone = vi.fn();
    const onAllDone = vi.fn();

    renderWith(
      <PipelinePanel
        tracking={TRACKING}
        onPhaseDone={onPhaseDone}
        onAllDone={onAllDone}
        onDismiss={vi.fn()}
      />,
    );

    expect(await screen.findByText("2 metadata · 2 transcripts fetched")).toBeInTheDocument();
    expect(screen.getByText("2 transcripts in ES (2 new · 0 cached)")).toBeInTheDocument();
    expect(screen.getByText("2 transcripts · 20 chunks · 20 points")).toBeInTheDocument();
    expect(screen.getByText("5 nodes · 3 rels · 1 merged")).toBeInTheDocument();
    expect(screen.getAllByText("Done").length).toBeGreaterThanOrEqual(4);
    await waitFor(() => expect(onPhaseDone).toHaveBeenCalledTimes(4)); // both split bars share the task
    await waitFor(() => expect(onAllDone).toHaveBeenCalledTimes(1));
  });

  it("stops through the confirm dialog and reads Cancelled", async () => {
    const user = userEvent.setup();
    taskState.state = "RUNNING";
    taskState.meta = { phase: "transcription", current: 3, total: 10 };
    renderWith(
      <PipelinePanel tracking={TRACKING} onPhaseDone={vi.fn()} onAllDone={vi.fn()} onDismiss={vi.fn()} />,
    );

    await user.click(await screen.findByRole("button", { name: "Stop" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Stop the pipeline?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stop" }));

    await waitFor(() => expect(screen.getAllByText("Cancelled").length).toBeGreaterThanOrEqual(2));
    expect(screen.getAllByText("Stopped by user — partial writes preserved.").length).toBeGreaterThanOrEqual(
      2,
    );
  });

  it("wipes through the confirm dialog and prints the summary", async () => {
    const user = userEvent.setup();
    renderWith(
      <PipelinePanel tracking={TRACKING} onPhaseDone={vi.fn()} onAllDone={vi.fn()} onDismiss={vi.fn()} />,
    );

    await user.click(await screen.findByRole("button", { name: "Wipe cache" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Wipe cache for these videos?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Wipe" }));

    expect(
      await screen.findByText("Wiped: ES 2m + 5t · Qdrant 12 · Neo4j 30d + 2v + 8e"),
    ).toBeInTheDocument();
  });

  it("dismiss goes through the confirm dialog and calls onDismiss", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    renderWith(
      <PipelinePanel tracking={TRACKING} onPhaseDone={vi.fn()} onAllDone={vi.fn()} onDismiss={onDismiss} />,
    );

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Dismiss this pipeline panel?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(onDismiss).toHaveBeenCalledTimes(1));
  });

  it("retry queues a rerun and pushes the fresh phase ids", async () => {
    const user = userEvent.setup();
    taskState.state = "SUCCESS";
    taskState.result = {
      total_videos: 2,
      metadata: { indexed: 2 },
      transcriptions: { indexed: 2 },
      completed_ids: TRACKING.videoIds,
    };
    streamState.qdrant = { state: "SUCCESS", meta: { completed_ids: TRACKING.videoIds }, result: null };
    streamState.neo4j = { state: "SUCCESS", meta: { completed_ids: TRACKING.videoIds }, result: null };
    renderWith(
      <PipelinePanel tracking={TRACKING} onPhaseDone={vi.fn()} onAllDone={vi.fn()} onDismiss={vi.fn()} />,
    );

    const retry = await screen.findByRole("button", { name: "Retry" });
    await waitFor(() => expect(retry).toBeEnabled());
    await user.click(retry);
    await waitFor(() => expect(nav.push).toHaveBeenCalledTimes(1));
    expect(String(nav.push.mock.calls[0]?.[0])).toContain("/ycs/ingestion?extract=e2");
  });
});
