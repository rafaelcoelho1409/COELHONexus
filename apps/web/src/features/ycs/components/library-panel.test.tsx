import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";
import { LibraryPanel } from "./library-panel";
import { YcsOverlaysProvider } from "./overlays";

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

const ITEMS = [
  {
    video_id: "vid1aaaaaaa",
    title: "Redis in practice",
    channel: "Backend Weekly",
    status: "done",
    view_count: 1500,
    duration: 95,
    like_count: 42,
    upload_date: "20240105",
    transcript_length: 9000,
    transcript_langs: ["en", "de"],
    entity_count: 12,
    thumbnail: "https://i.ytimg.com/vi/vid1aaaaaaa/hqdefault.jpg",
    webpage_url: "https://youtube.com/watch?v=vid1aaaaaaa",
  },
  {
    video_id: "vid2aaaaaaa",
    title: "Postgres deep dive",
    channel: "Backend Weekly",
    status: "partial",
    view_count: 30_000,
    webpage_url: "https://youtube.com/watch?v=vid2aaaaaaa",
  },
];

const FACETS = {
  statuses: [
    { key: "done", label: "Done", count: 1 },
    { key: "partial", label: "Partial", count: 1 },
  ],
  channels: [{ key: "uc-backend", label: "Backend Weekly", count: 2 }],
  langs: [{ key: "en", label: "English", count: 2 }],
};

/** The last query string the rows endpoint saw — assertions read it instead of counting polls. */
let lastRowsQuery = "";

function installHandlers() {
  server.use(
    http.get(`${API}/v1/ycs/admin/videos`, ({ request }) => {
      lastRowsQuery = new URL(request.url).search;
      const url = new URL(request.url);
      const q = url.searchParams.get("q") ?? "";
      const items = q ? ITEMS.filter((item) => item.title.includes(q)) : ITEMS;
      return HttpResponse.json({ items, total: items.length, returned: items.length, offset: 0, limit: 50 });
    }),
    http.get(`${API}/v1/ycs/admin/videos/facets`, () => HttpResponse.json(FACETS)),
    http.delete(`${API}/v1/ycs/admin/videos/:videoId`, ({ params }) => {
      const id = String(params.videoId);
      const index = ITEMS.findIndex((item) => item.video_id === id);
      if (index >= 0) ITEMS.splice(index, 1);
      return HttpResponse.json({ status: "wiped", summary: {} });
    }),
    http.post(`${API}/v1/ycs/admin/videos/bulk-delete`, async ({ request }) => {
      const body = (await request.json()) as { video_ids: string[] };
      for (const id of body.video_ids) {
        const index = ITEMS.findIndex((item) => item.video_id === id);
        if (index >= 0) ITEMS.splice(index, 1);
      }
      return HttpResponse.json({ status: "wiped", summary: {} });
    }),
    http.post(`${API}/v1/ycs/content/videos/pipeline`, () =>
      HttpResponse.json({
        status: "queued",
        phases: { extract: "e9", qdrant: "e9", neo4j: "e9" },
        video_ids: ["vid1aaaaaaa"],
      }),
    ),
  );
}

describe("LibraryPanel", () => {
  beforeEach(() => {
    installHandlers();
    ITEMS.splice(0, ITEMS.length, ...structuredClone(FIXTURE_ITEMS));
  });

  it("renders rows with status, meta and stats lines, and facet options", async () => {
    renderWith(<LibraryPanel refreshTick={0} />);

    expect(await screen.findByRole("link", { name: /^Redis in practice/ })).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(
      screen.getByText("Backend Weekly · 1.5K views · 1:35 · 42 likes · 2024-01-05"),
    ).toBeInTheDocument();
    expect(screen.getByText("9.0K chars · en, de · 12 entities")).toBeInTheDocument();
    const status = screen.getByLabelText("Status") as HTMLSelectElement;
    expect(within(status).getByText("Done (1)")).toBeInTheDocument();
    expect(within(status).getByText("Partial (1)")).toBeInTheDocument();
    expect(screen.getByText("· 2 videos")).toBeInTheDocument();
  });

  it("filters through the facet selects and the debounced search", async () => {
    const user = userEvent.setup();
    renderWith(<LibraryPanel refreshTick={0} />);
    await screen.findByRole("link", { name: /^Redis in practice/ });

    await user.selectOptions(screen.getByLabelText("Status"), "partial");
    await waitFor(() => expect(lastRowsQuery).toContain("status=partial"));

    await user.type(screen.getByLabelText("Search"), "Redis");
    await waitFor(
      () => {
        expect(lastRowsQuery).toContain("q=Redis");
      },
      { timeout: 1500 },
    );
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /^Postgres deep dive/ })).not.toBeInTheDocument(),
    );
  });

  it("selects rows, shows the bulk bar and bulk-deletes through the confirm dialog", async () => {
    const user = userEvent.setup();
    renderWith(<LibraryPanel refreshTick={0} />);
    await screen.findByRole("link", { name: /^Redis in practice/ });

    await user.click(screen.getByRole("checkbox", { name: "Select Redis in practice" }));
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Select all videos on this page" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Delete 2 videos?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(screen.queryByText("2 selected")).not.toBeInTheDocument());
    expect(screen.getByText("No videos match the current filters."));
  });

  it("deletes one row through its trash button and the confirm dialog", async () => {
    const user = userEvent.setup();
    renderWith(<LibraryPanel refreshTick={0} />);
    await screen.findByRole("link", { name: /^Redis in practice/ });

    await user.click(screen.getByRole("button", { name: "Delete Redis in practice" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Delete this video?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /^Redis in practice/ })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: /^Postgres deep dive/ })).toBeInTheDocument();
  });

  it("re-ingests the selection into the pipeline and routes to the ingestion stage", async () => {
    const user = userEvent.setup();
    renderWith(<LibraryPanel refreshTick={0} />);
    await screen.findByRole("link", { name: /^Redis in practice/ });

    await user.click(screen.getByRole("checkbox", { name: "Select Redis in practice" }));
    await user.click(screen.getByRole("button", { name: "Re-ingest" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledTimes(1));
    expect(String(nav.push.mock.calls[0]?.[0])).toContain("/ycs/ingestion?extract=e9");
  });

  it("errors read as a neutral line", async () => {
    server.use(
      http.get(`${API}/v1/ycs/admin/videos`, () =>
        HttpResponse.json({ detail: "the library is closed" }, { status: 500 }),
      ),
    );
    renderWith(<LibraryPanel refreshTick={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the library — the library is closed",
    );
  });
});

const FIXTURE_ITEMS = structuredClone(ITEMS);
