import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { SearchTab } from "./search-tab";

const ORIGIN = "http://nexus.test";
const API = "*/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  usePathname: () => "/ycs",
  useSearchParams: () => new URLSearchParams(),
  useSelectedLayoutSegment: () => null,
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  localStorage.clear();
  client.setConfig({ baseUrl: ORIGIN });
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWith(onRoute?: (routed: { mode: string; items: string[] }) => void) {
  return render(
    <QueryProvider>
      <SearchTab onRoute={onRoute ?? (() => {})} />
    </QueryProvider>,
  );
}

const RESULTS = [
  {
    id: "vidA",
    kind: "video",
    title: "Hook tutorial",
    url: "https://youtube.com/watch?v=vidA",
    view_count: 1200,
    channel: "React Channel",
    upload_date: "20240105",
    duration_string: "12:34",
  },
  { id: "chaB", kind: "channel", title: "React Channel", url: "https://youtube.com/@react", video_count: 42 },
  {
    id: "plC",
    kind: "playlist",
    title: "Hooks playlist",
    url: "https://youtube.com/playlist?list=plC",
    video_count: 7,
  },
];

function searchHandler(results: unknown[], extra?: Record<string, unknown>) {
  let body: unknown = null;
  const handler = http.post(`${API}/v1/ycs/content/search`, async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({
      query: "hooks",
      total: results.length,
      results,
      fetched_for: results.length,
      elapsed_s: 1.2,
      ...extra,
    });
  });
  return { handler, read: () => body };
}

async function submit(query: string) {
  await userEvent.type(screen.getByLabelText("Search"), query);
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
}

describe("SearchTab", () => {
  it("renders results with range text and per-kind rows", async () => {
    const { handler } = searchHandler(RESULTS);
    server.use(handler);
    renderWith();
    await submit("hooks");
    expect(await screen.findByText("1–3 of 3")).toBeInTheDocument();
    expect(screen.getByText("Hook tutorial")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "vidA" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "chaB" })).toBeInTheDocument();
  });

  it("shows the API error in the live status line", async () => {
    server.use(
      http.post(`${API}/v1/ycs/content/search`, () =>
        HttpResponse.json({ detail: "yt-dlp subprocess failed" }, { status: 500 }),
      ),
    );
    renderWith();
    await submit("hooks");
    expect(await screen.findByText("yt-dlp subprocess failed")).toBeInTheDocument();
  });

  it("routes selections per kind and drops only the routed rows", async () => {
    const { handler } = searchHandler(RESULTS);
    server.use(handler);
    const onRoute = vi.fn();
    renderWith(onRoute);
    await submit("hooks");
    await screen.findByText("Hook tutorial");

    await userEvent.click(screen.getByRole("checkbox", { name: "Select Hook tutorial" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select React Channel" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Hooks playlist" }));

    await userEvent.click(screen.getByRole("button", { name: "→ 1 Channel" }));
    expect(onRoute).toHaveBeenNthCalledWith(1, { mode: "channel", items: ["chaB"] });
    await waitFor(() => expect(screen.getByRole("button", { name: "→ 1 Playlist" })).toBeEnabled());

    await userEvent.click(screen.getByRole("button", { name: "→ 1 Playlist" }));
    expect(onRoute).toHaveBeenNthCalledWith(2, { mode: "playlist", items: ["plC"] });
    await waitFor(() => expect(screen.getByRole("button", { name: "→ 1 Videos" })).toBeEnabled());

    await userEvent.click(screen.getByRole("button", { name: "→ 1 Videos" }));
    expect(onRoute).toHaveBeenNthCalledWith(3, { mode: "videos", items: ["vidA"] });
    await waitFor(() => expect(screen.queryByText(/selected/)).not.toBeInTheDocument());
  });

  it("sends filters and page size in the request body", async () => {
    const { handler, read } = searchHandler(RESULTS);
    server.use(handler);
    renderWith();

    await userEvent.click(screen.getByRole("button", { name: "Filters" }));
    await userEvent.type(screen.getByLabelText("Search"), "hooks");
    await userEvent.type(screen.getByLabelText("Title contains"), "hooks");
    await userEvent.click(screen.getByRole("checkbox", { name: "Newest first" }));
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await screen.findByText("1–3 of 3");
    await waitFor(() =>
      expect(read()).toMatchObject({
        query: "hooks",
        title_contains: "hooks",
        sort_by_date: true,
        max_results: 25,
      }),
    );
  });
});
