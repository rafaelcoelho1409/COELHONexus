import { render, screen, waitFor, within } from "@testing-library/react";
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
    expect(await screen.findByText(/1–3 of 3 · 1\.2 s/)).toBeInTheDocument();
    expect(screen.getByText("Hook tutorial")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "vidA" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "chaB" })).toBeInTheDocument();
  });

  it("says what failed in an alert, notes it in the live status, and searches again on Try again", async () => {
    server.use(
      http.post(`${API}/v1/ycs/content/search`, () =>
        HttpResponse.json({ detail: "yt-dlp subprocess failed" }, { status: 500 }),
      ),
    );
    renderWith();
    await submit("hooks");
    expect(await screen.findByRole("alert")).toHaveTextContent("yt-dlp subprocess failed");
    expect(screen.getByRole("status")).toHaveTextContent("Search failed");

    server.use(searchHandler(RESULTS).handler);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Hook tutorial")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows no results card chrome before the first search, only what to do", () => {
    renderWith();
    expect(screen.getByText("Search YouTube")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select all visible results" })).not.toBeInTheDocument();
  });

  it("says when nothing matched, and offers to reset the filters that were set", async () => {
    server.use(searchHandler([]).handler);
    renderWith();
    await userEvent.click(screen.getByRole("button", { name: "More filters" }));
    await userEvent.type(screen.getByLabelText("Title contains"), "zzz");
    await submit("hooks");
    expect(await screen.findByText("No matches for “hooks”")).toBeInTheDocument();
    expect(screen.getByText("The filters may be too narrow.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset filters", hidden: false })).toBeInTheDocument();
  });

  it("shows the filters a search ran with as chips, and a chip removes its filter and searches again", async () => {
    const { handler, read } = searchHandler(RESULTS);
    server.use(handler);
    renderWith();
    const more = screen.getByRole("button", { name: "More filters" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    await userEvent.type(screen.getByLabelText("Title contains"), "hooks");
    await userEvent.click(screen.getByRole("radio", { name: "Videos" })); // a quick filter, always in view
    await submit("hooks");

    const chips = await screen.findByRole("list", { name: "Active filters" });
    expect(within(chips).getByRole("button", { name: "Remove filter: Title: hooks" })).toBeInTheDocument();
    expect(within(chips).getByRole("button", { name: "Remove filter: Kind: Videos" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /More filters/ })).toHaveAttribute("aria-expanded", "false"); // a search closes the panel
    expect(screen.getByRole("button", { name: /More filters/ })).toHaveTextContent("1"); // only the panel's filters count

    await userEvent.click(within(chips).getByRole("button", { name: "Remove filter: Title: hooks" }));
    await waitFor(() => expect(read()).not.toHaveProperty("title_contains"));
    expect(read()).toMatchObject({ query: "hooks", kind_filter: "video" });

    await userEvent.click(within(chips).getByRole("button", { name: "Clear all" }));
    await waitFor(() => expect(read()).not.toHaveProperty("kind_filter"));
  });

  it("keeps kind, duration and newest first in view, and applies them with Search like the rest", async () => {
    const { handler, read } = searchHandler(RESULTS);
    server.use(handler);
    renderWith();
    // no panel to open: the three quick filters are on the page
    expect(screen.getByRole("radio", { name: "All" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Channels" }));
    await userEvent.selectOptions(screen.getByLabelText("Duration"), "Over 20 minutes");
    await userEvent.click(screen.getByRole("checkbox", { name: "Newest first" }));
    await submit("hooks");

    await screen.findByText(/1–3 of 3/);
    await waitFor(() =>
      expect(read()).toMatchObject({
        query: "hooks",
        kind_filter: "channel",
        duration: "Over 20 minutes",
        sort_by_date: true,
      }),
    );
    // and Clear all puts the quick filters back, the kind group on "All"
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.getByRole("radio", { name: "All" })).toBeChecked();
    await waitFor(() => expect(read()).not.toHaveProperty("kind_filter"));
  });

  it("searches from the foot of the filter panel, without going back up to the Search button", async () => {
    const { handler, read } = searchHandler(RESULTS);
    server.use(handler);
    renderWith();
    await userEvent.type(screen.getByLabelText("Search"), "hooks");
    await userEvent.click(screen.getByRole("button", { name: "More filters" }));
    await userEvent.type(screen.getByLabelText("Channel name"), "React");
    await userEvent.click(screen.getByRole("button", { name: "Apply and search" }));
    await screen.findByText(/1–3 of 3/);
    await waitFor(() => expect(read()).toMatchObject({ query: "hooks", channel_name: "React" }));
  });

  it("selects a Shift range, with the master box going indeterminate and then checked", async () => {
    const rows = Array.from({ length: 5 }, (_, i) => ({
      id: `v${i}`,
      kind: "video",
      title: `Clip ${i}`,
      url: `https://youtube.com/watch?v=v${i}`,
    }));
    server.use(searchHandler(rows).handler);
    renderWith();
    await submit("clips");
    await screen.findByText("Clip 0");

    const master = screen.getByRole("checkbox", { name: "Select all visible results" });
    const user = userEvent.setup(); // one session, so the held Shift key reaches the click
    await user.click(screen.getByRole("checkbox", { name: "Select Clip 1" }));
    expect(master).toHaveProperty("indeterminate", true);
    await user.keyboard("{Shift>}");
    await user.click(screen.getByRole("checkbox", { name: "Select Clip 3" }));
    await user.keyboard("{/Shift}");
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("3 selected");
    expect(screen.getByRole("checkbox", { name: "Select Clip 2" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select Clip 0" })).not.toBeChecked();

    await userEvent.click(master); // some selected: the first click selects every row on the page
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("5 selected");
    expect(master).toBeChecked();
    await userEvent.click(master);
    expect(screen.queryByRole("region", { name: "Selection" })).not.toBeInTheDocument();
  });

  it("asks for the next page with a larger window, keeps the cache for Previous, and moves focus to the results", async () => {
    localStorage.setItem("ycs:results:page_size", "10");
    const bodies: Array<{ max_results?: number }> = [];
    server.use(
      http.post(`${API}/v1/ycs/content/search`, async ({ request }) => {
        const body = (await request.json()) as { max_results?: number };
        bodies.push(body);
        const results = Array.from({ length: body.max_results ?? 0 }, (_, i) => ({
          id: `v${i}`,
          kind: "video",
          title: `Clip ${i}`,
          url: `https://youtube.com/watch?v=v${i}`,
        }));
        return HttpResponse.json({
          query: "clips",
          total: results.length,
          results,
          fetched_for: results.length,
          elapsed_s: 0,
        });
      }),
    );
    renderWith();
    await submit("clips");
    expect(await screen.findByText("1–10 of 10+")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next 10 →" }));
    expect(await screen.findByText("11–20 of 20+")).toBeInTheDocument();
    expect(bodies.map((b) => b.max_results)).toEqual([10, 20]);
    expect(screen.getByRole("heading", { name: "Search results" })).toHaveFocus();

    await userEvent.click(screen.getByRole("button", { name: "← Previous" }));
    expect(screen.getByText("1–10 of 20+")).toBeInTheDocument();
    expect(bodies).toHaveLength(2); // Previous reads the cache
  });

  it("jumps to the query with the / key, but not while typing in a field", async () => {
    renderWith();
    const query = screen.getByLabelText("Search");
    await userEvent.keyboard("/");
    expect(query).toHaveFocus();
    await userEvent.keyboard("a/b");
    expect(query).toHaveValue("a/b"); // the slash typed in the box is text
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

    await userEvent.click(screen.getByRole("button", { name: "More filters" }));
    await userEvent.type(screen.getByLabelText("Search"), "hooks");
    await userEvent.type(screen.getByLabelText("Title contains"), "hooks");
    await userEvent.click(screen.getByRole("checkbox", { name: "Newest first" }));
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await screen.findByText(/1–3 of 3/);
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
