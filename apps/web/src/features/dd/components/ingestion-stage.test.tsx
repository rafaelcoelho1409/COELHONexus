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
const DONE = { phase: "ingest", tier: "finalize", current: 0, total: 0, last_url: "", status: "done" };

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
    http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
    http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])),
    http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
  );
}

describe("IngestionStage — the ?run= address", () => {
  it("drops ?run= once the run is done, and stays put even while the active list still names that run", async () => {
    // The active list is cached for a few seconds, so right after a run ends it can still list the run.
    serve(() => [{ slug: "alpha", run_id: RUN, progress: { ...DONE, status: "running" } }]);
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("/dd/ingestion?slug=alpha"));
    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("run="));
    // … and it stays: no put-back, no flicker.
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(screen.getByTestId("url")).not.toHaveTextContent("run=");
    expect(replace.mock.calls.length).toBeLessThanOrEqual(1);
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
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
    );
    render(<Page start="/dd/ingestion?slug=alpha" />);
    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("run=r9"));
    expect(replace).toHaveBeenCalledTimes(1);
  });

  it("names the framework on the progress box, with its logo, before it has ever been ingested", async () => {
    server.use(
      http.get(`${ORIGIN}/api/v1/dd/resolver`, () => HttpResponse.json(CATALOG)),
      http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: [] })),
      http.get(`${ORIGIN}/api/v1/dd/runs/r5`, () =>
        HttpResponse.json({ progress: { ...DONE, status: "running", tier: "llms_full" }, post: null }),
      ),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])), // nothing in the library yet
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
    );
    render(<Page start="/dd/ingestion?slug=alpha&run=r5" />);

    const box = await screen.findByRole("region", { name: "Ingestion progress" });
    expect(await within(box).findByText("Alpha Framework")).toBeInTheDocument();
    expect(box.querySelector("img")).toHaveAttribute("src", "https://logos.test/alpha.svg");
    expect(within(box).getByText("Fetching documentation bundle")).toBeInTheDocument();
  });

  it("does not adopt a run of another framework", async () => {
    serve(() => [{ slug: "beta", run_id: "r7", progress: { ...DONE, status: "running" } }]);
    render(<Page start="/dd/ingestion?slug=alpha" />);
    await screen.findByLabelText("Search pages");
    expect(replace).not.toHaveBeenCalled();
  });
});

describe("IngestionStage — cancelling and how a run ends", () => {
  const RUNNING = {
    phase: "ingest",
    tier: "llms_full",
    current: 0,
    total: 1,
    last_url: "",
    status: "running",
  };

  /** One run, whose status the test controls, and a cancel endpoint that answers like the real one. */
  function serveRun(progress: () => Record<string, unknown>, cancel?: () => Response) {
    server.use(
      http.get(`${ORIGIN}/api/v1/dd/resolver`, () => HttpResponse.json(CATALOG)),
      http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: [] })),
      http.get(`${ORIGIN}/api/v1/dd/runs/${RUN}`, () =>
        HttpResponse.json({ progress: progress(), post: null }),
      ),
      http.post(
        `${ORIGIN}/api/v1/dd/runs/${RUN}/cancel`,
        cancel ?? (() => HttpResponse.json({ run_id: RUN, status: "cancel_requested" })),
      ),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/manifest`, () => HttpResponse.json(MANIFEST)),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([])),
      http.get(`${ORIGIN}/api/v1/dd/ingestion/alpha/pages/*`, () => HttpResponse.json({ body: "x" })),
    );
  }

  it("says 'Cancelling…' from the click until the run really ends, not only while the request is in flight", async () => {
    // The worker takes a while to stop: the status still reads "running" after the cancel request has returned.
    serveRun(() => RUNNING);
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);
    const box = await screen.findByRole("region", { name: "Ingestion progress" });
    expect(await within(box).findByText("running")).toBeInTheDocument();

    await userEvent.click(within(box).getByRole("button", { name: "Cancel run" }));

    const button = await within(box).findByRole("button", { name: "Cancelling…" });
    expect(button).toBeDisabled();
    expect(within(box).getByTestId("run-cancelling")).toHaveTextContent("Cancelling");
    // Long after the request has returned (the status poll runs every 1.5 s) it must not fall back to "running".
    await new Promise((resolve) => setTimeout(resolve, 1_700));
    expect(within(box).getByText("cancelling…")).toBeInTheDocument();
    expect(within(box).queryByText("running")).not.toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "Cancelling…" })).toBeDisabled();
  });

  it("says so, and gives the button back, when the cancel request itself fails", async () => {
    serveRun(
      () => RUNNING,
      () => HttpResponse.json({ detail: "redis unavailable" }, { status: 503 }),
    );
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);
    const box = await screen.findByRole("region", { name: "Ingestion progress" });
    await userEvent.click(await within(box).findByRole("button", { name: "Cancel run" }));

    expect(await within(box).findByRole("alert")).toHaveTextContent(
      "Could not cancel the run — redis unavailable",
    );
    expect(within(box).getByRole("button", { name: "Cancel run" })).toBeEnabled();
    expect(within(box).queryByTestId("run-cancelling")).not.toBeInTheDocument();
  });

  it("announces a cancelled run and leaves ?run=", async () => {
    serveRun(() => ({ ...RUNNING, status: "cancelled" }));
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);

    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("run="));
    expect(await screen.findByText("Ingestion cancelled.")).toBeInTheDocument();
  });

  it("keeps a failed run on screen with its reason until it is dismissed", async () => {
    serveRun(() => ({ ...RUNNING, status: "failed", error: "RuntimeError: no declared source worked" }));
    render(<Page start={`/dd/ingestion?slug=alpha&run=${RUN}`} />);

    const box = await screen.findByRole("region", { name: "Ingestion progress" });
    expect(await within(box).findByText("RuntimeError: no declared source worked")).toBeInTheDocument();
    expect(within(box).getByText("failed")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(screen.getByTestId("url")).toHaveTextContent(`run=${RUN}`); // not dropped behind the reader's back
    expect(within(box).queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();

    await userEvent.click(within(box).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("run="));
    expect(screen.queryByRole("region", { name: "Ingestion progress" })).not.toBeInTheDocument();
  });
});
