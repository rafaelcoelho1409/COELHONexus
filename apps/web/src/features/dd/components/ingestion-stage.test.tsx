import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { useEffect, useState } from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";
import { IngestionStage } from "./ingestion-stage";
import { DdOverlaysProvider } from "./overlays";

const ORIGIN = "http://nexus.test";
const RUN = "dc225f71cb4d4adb95933f33871b62fc";

// `router.replace` must really move the URL, or the loop this guards against cannot happen in a test.
let moveTo: (url: string) => void = () => {};
const replace = vi.fn((url: string) => moveTo(url));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace, refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  usePathname: () => "/dd/ingestion",
  useSearchParams: () => new URLSearchParams(),
}));

const MANIFEST = {
  framework_name: "Alpha",
  framework_slug: "alpha",
  total_bytes: 640,
  tier_kind: "llms_full",
  ingested_at: Math.floor(Date.now() / 1000) - 60,
  entries: [
    {
      idx: 0,
      key: null,
      title: "One",
      slug: null,
      tier: "llms-full",
      bytes: 320,
      url: null,
      source_path: null,
      section: null,
      notes: null,
    },
  ],
};
const LIBRARY_ITEM = {
  slug: "alpha",
  framework_name: "Alpha Framework",
  framework_slug: "alpha",
  page_count: 1,
  ingested_at: Math.floor(Date.now() / 1000) - 60,
  tier_kind: "llms_full",
  logo: null,
  logos: null,
};
const DONE = { phase: "ingest", tier: "finalize", current: 0, total: 0, last_url: "", status: "done" };
const NO_USAGE = {
  total: { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0, errors: 0 },
  by_node: {},
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  replace.mockClear();
  client.setConfig({ baseUrl: ORIGIN });
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** A page whose address is state, as in the browser: the stage reads `slug` and `run` from it. */
function Page({ start }: { start: string }) {
  const [url, setUrl] = useState(start);
  useEffect(() => {
    moveTo = setUrl;
  }, []);
  const params = new URL(url, "http://nexus.test").searchParams;
  return (
    <QueryProvider>
      <DdOverlaysProvider>
        <p data-testid="url">{url}</p>
        <IngestionStage slug={params.get("slug")} runId={params.get("run")} />
      </DdOverlaysProvider>
    </QueryProvider>
  );
}

const CATALOG = [
  {
    slug: "alpha",
    name: "Alpha Framework",
    category: "Dev",
    tier: 1,
    logo: "https://logos.test/alpha.svg",
    logos: null,
  },
];

function serve(activeRuns: () => unknown[]) {
  server.use(
    http.get(`${ORIGIN}/api/v1/dd/resolver`, () => HttpResponse.json(CATALOG)),
    http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: activeRuns() })),
    http.get(`${ORIGIN}/api/v1/dd/runs/${RUN}`, () => HttpResponse.json({ progress: DONE, post: null })),
    http.get(`${ORIGIN}/api/v1/dd/runs/*/llm-counters`, () => HttpResponse.json(NO_USAGE)),
    http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
    http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])),
    http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
  );
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("IngestionStage — the ?run= address", () => {
  it("keeps a finished run on screen, and does not adopt it again while the active list still names it", async () => {
    // The active list is cached for a few seconds, so right after a run ends it can still list the run.
    serve(() => [{ slug: "alpha", run_id: RUN, progress: { ...DONE, status: "running" } }]);
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);

    expect(await screen.findByText("Ingestion complete.")).toBeInTheDocument();
    await sleep(600);
    expect(screen.getByTestId("url")).toHaveTextContent(`run=${RUN}`);
    expect(replace).not.toHaveBeenCalled();
  });

  it("still adopts a run that is really in flight when the page is opened without ?run=", async () => {
    server.use(
      http.get(`${ORIGIN}/api/v1/dd/runs/active`, () =>
        HttpResponse.json({
          active: [{ slug: "alpha", run_id: "r9", progress: { ...DONE, status: "running" } }],
        }),
      ),
      http.get(`${ORIGIN}/api/v1/dd/runs/r9`, () =>
        HttpResponse.json({ progress: { ...DONE, status: "running", tier: "llms_full" }, post: null }),
      ),
      http.get(`${ORIGIN}/api/v1/dd/runs/*/llm-counters`, () => HttpResponse.json(NO_USAGE)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
    );
    render(<Page start="/dd/ingestion?slug=alpha" />);
    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("run=r9"));
    expect(replace).toHaveBeenCalledTimes(1);
  });

  it("does not adopt a run of another framework", async () => {
    serve(() => [{ slug: "beta", run_id: "r7", progress: { ...DONE, status: "running" } }]);
    render(<Page start="/dd/ingestion?slug=alpha" />);
    await screen.findByLabelText("Search pages");
    expect(replace).not.toHaveBeenCalled();
  });
});

describe("IngestionStage — the Pipeline tab", () => {
  const STAGES = {
    extraction: {
      status: "running",
      tier: "llms_full",
      current: 3,
      total: 10,
      last_url: "https://docs.test/intro",
      error: null,
    },
    agentic: {
      status: "waiting",
      current: 0,
      total: 0,
      current_node: null,
      node_status: {},
      node_progress: {},
    },
  };

  it("shows the two progress cards and the Agentic AI graph of a run in flight", async () => {
    server.use(
      http.get(`${ORIGIN}/api/v1/dd/resolver`, () => HttpResponse.json(CATALOG)),
      http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: [] })),
      http.get(`${ORIGIN}/api/v1/dd/runs/r5`, () =>
        HttpResponse.json({
          progress: { ...DONE, status: "running", tier: "llms_full", current: 3, total: 10, stages: STAGES },
          post: null,
        }),
      ),
      http.get(`${ORIGIN}/api/v1/dd/runs/*/llm-counters`, () => HttpResponse.json(NO_USAGE)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
    );
    render(<Page start="/dd/ingestion?slug=alpha&run=r5" />);

    const extraction = await screen.findByRole("region", { name: "Documentation extraction progress" });
    expect(await within(extraction).findByText("Processing")).toBeInTheDocument();
    expect(within(extraction).getByText("3 of 10 pages · 30%")).toBeInTheDocument();
    expect(within(extraction).getByText("Fetching documentation bundle")).toBeInTheDocument();
    expect(within(extraction).getByText("https://docs.test/intro")).toBeInTheDocument();
    expect(within(extraction).getByRole("button", { name: "Start documentation extraction" })).toBeDisabled();
    expect(within(extraction).getByRole("button", { name: "Stop documentation extraction" })).toBeEnabled();

    const agentic = screen.getByRole("region", { name: "Agentic AI progress" });
    expect(within(agentic).getByText("Waiting")).toBeInTheDocument();

    const pipeline = screen.getByRole("region", { name: "Agentic pipeline stages" });
    await userEvent.click(within(pipeline).getByRole("button", { name: "List" }));
    const nodes = within(pipeline).getByRole("list", { name: "Agentic pipeline nodes" });
    expect(within(nodes).getAllByRole("listitem")).toHaveLength(3);
    expect(within(nodes).getByRole("button", { name: /Audit pages/ })).toBeInTheDocument();
    expect(within(pipeline).getByRole("region", { name: "Page progress" })).toBeInTheDocument();
  });

  it("starts an ingestion that has never run, and follows the run it queued", async () => {
    serve(() => []);
    server.use(
      http.post(`${ORIGIN}/api/v1/dd/runs`, () => HttpResponse.json({ status: "queued", run_id: "r1" })),
      http.get(`${ORIGIN}/api/v1/dd/runs/r1`, () =>
        HttpResponse.json({ progress: { ...DONE, status: "running", tier: "llms_full" }, post: null }),
      ),
    );
    render(<Page start="/dd/ingestion?slug=alpha" />);

    const extraction = await screen.findByRole("region", { name: "Documentation extraction progress" });
    const start = within(extraction).getByRole("button", { name: "Start documentation extraction" });
    expect(start).toBeEnabled();
    expect(within(extraction).getByRole("button", { name: "Wipe ingested documentation" })).toBeDisabled();
    await userEvent.click(start);

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("run=r1"));
  });

  it("will not start again over a framework that is already in the library", async () => {
    serve(() => []);
    server.use(http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([LIBRARY_ITEM])));
    render(<Page start="/dd/ingestion?slug=alpha" />);

    const extraction = await screen.findByRole("region", { name: "Documentation extraction progress" });
    await waitFor(() =>
      expect(
        within(extraction).getByRole("button", { name: "Start documentation extraction" }),
      ).toBeDisabled(),
    );
    expect(within(extraction).getByText("Complete")).toBeInTheDocument();
    expect(within(extraction).getByText("1 of 1 pages · 100%")).toBeInTheDocument();
  });

  it("wipes the ingested documentation after a confirmation", async () => {
    serve(() => []);
    let deleted = false;
    server.use(
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([LIBRARY_ITEM])),
      http.delete(`${ORIGIN}/api/v1/dd/ingestion/alpha`, () => {
        deleted = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    render(<Page start="/dd/ingestion?slug=alpha" />);

    const extraction = await screen.findByRole("region", { name: "Documentation extraction progress" });
    const wipe = within(extraction).getByRole("button", { name: "Wipe ingested documentation" });
    await waitFor(() => expect(wipe).toBeEnabled());
    await userEvent.click(wipe);

    expect(await screen.findByText("Wipe ingested documentation?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Wipe" }));

    expect(await screen.findByText("Wiped Alpha Framework's ingested documentation.")).toBeInTheDocument();
    expect(deleted).toBe(true);
  });
});

describe("IngestionStage — stopping and how a run ends", () => {
  const RUNNING = {
    phase: "ingest",
    tier: "llms_full",
    current: 0,
    total: 1,
    last_url: "",
    status: "running",
    stages: {
      extraction: { status: "running", tier: "llms_full", current: 0, total: 1, last_url: "", error: null },
      agentic: {
        status: "waiting",
        current: 0,
        total: 0,
        current_node: null,
        node_status: {},
        node_progress: {},
      },
    },
  };

  /** One run, whose status the test controls, and a cancel endpoint that answers like the real one. */
  function serveRun(progress: () => Record<string, unknown>, cancel?: () => Response) {
    server.use(
      http.get(`${ORIGIN}/api/v1/dd/resolver`, () => HttpResponse.json(CATALOG)),
      http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: [] })),
      http.get(`${ORIGIN}/api/v1/dd/runs/${RUN}`, () =>
        HttpResponse.json({ progress: progress(), post: null }),
      ),
      http.get(`${ORIGIN}/api/v1/dd/runs/*/llm-counters`, () => HttpResponse.json(NO_USAGE)),
      http.post(
        `${ORIGIN}/api/v1/dd/runs/${RUN}/cancel`,
        cancel ?? (() => HttpResponse.json({ run_id: RUN, status: "cancel_requested" })),
      ),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
    );
  }

  it("says 'Stopping…' from the click until the run really ends, not only while the request is in flight", async () => {
    // The worker takes a while to stop: the status still reads "running" after the cancel request has returned.
    serveRun(() => RUNNING);
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);
    const card = await screen.findByRole("region", { name: "Documentation extraction progress" });
    expect(await within(card).findByText("Processing")).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Stop documentation extraction" }));

    const button = await within(card).findByRole("button", { name: "Stop documentation extraction" });
    expect(button).toHaveTextContent("Stopping…");
    expect(button).toBeDisabled();
    // Long after the request has returned (the status poll runs every 1.5 s) it must not fall back to "running".
    await sleep(1_700);
    expect(within(card).getByText("Stopping")).toBeInTheDocument();
    expect(within(card).queryByText("Processing")).not.toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Stop documentation extraction" })).toBeDisabled();
    expect(
      within(screen.getByRole("region", { name: "Agentic AI progress" })).getByText(
        "Stop requested; active page work is draining before the run ends.",
      ),
    ).toBeInTheDocument();
  });

  it("says so, and gives the button back, when the cancel request itself fails", async () => {
    serveRun(
      () => RUNNING,
      () => HttpResponse.json({ detail: "redis unavailable" }, { status: 503 }),
    );
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);
    const card = await screen.findByRole("region", { name: "Documentation extraction progress" });
    await userEvent.click(await within(card).findByRole("button", { name: "Stop documentation extraction" }));

    expect(await within(card).findByRole("alert")).toHaveTextContent(
      "Could not cancel the run — redis unavailable",
    );
    const button = within(card).getByRole("button", { name: "Stop documentation extraction" });
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent("Stop");
    expect(button).not.toHaveTextContent("Stopping");
  });

  it("announces a cancelled run and keeps it on screen until it is dismissed", async () => {
    serveRun(() => ({ ...RUNNING, status: "cancelled" }));
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);

    // The notice and the card's own line.
    expect(await screen.findAllByText("Ingestion cancelled.")).toHaveLength(2);
    expect(screen.getByTestId("url")).toHaveTextContent(`run=${RUN}`);
    const card = screen.getByRole("region", { name: "Documentation extraction progress" });
    expect(within(card).getByRole("button", { name: "Retry documentation extraction" })).toBeEnabled();
  });

  it("keeps a failed run on screen with its reason until it is dismissed", async () => {
    serveRun(() => ({
      ...RUNNING,
      status: "failed",
      error: "RuntimeError: no declared source worked",
      stages: {
        ...RUNNING.stages,
        extraction: { ...RUNNING.stages.extraction, status: "failed" },
      },
    }));
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);

    const agentic = await screen.findByRole("region", { name: "Agentic AI progress" });
    expect(await within(agentic).findByText("RuntimeError: no declared source worked")).toBeInTheDocument();
    const card = screen.getByRole("region", { name: "Documentation extraction progress" });
    expect(within(card).getByText("Failed")).toBeInTheDocument();
    await sleep(400);
    expect(screen.getByTestId("url")).toHaveTextContent(`run=${RUN}`); // not dropped behind the reader's back
    expect(within(card).getByRole("button", { name: "Stop documentation extraction" })).toBeDisabled();

    await userEvent.click(within(card).getByRole("button", { name: "Dismiss run" }));
    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("run="));
    expect(within(card).queryByRole("button", { name: "Dismiss run" })).not.toBeInTheDocument();
  });
});
