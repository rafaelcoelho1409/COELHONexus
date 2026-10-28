import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { PlaylistTab } from "./playlist-tab";
import { VideosTab } from "./videos-tab";

const ORIGIN = "http://nexus.test";
const API = "*/api";

const nav = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
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
  nav.push.mockClear();
  client.setConfig({ baseUrl: ORIGIN });
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWith(children: React.ReactNode) {
  return render(<QueryProvider>{children}</QueryProvider>);
}

const V1 = {
  id: "vid1aaaaaaa",
  kind: "video",
  title: "Video one",
  url: "https://youtube.com/watch?v=vid1aaaaaaa",
};
const V2 = {
  id: "vid2aaaaaaa",
  kind: "video",
  title: "Video two",
  url: "https://youtube.com/watch?v=vid2aaaaaaa",
};
const V3 = {
  id: "vid3aaaaaaa",
  kind: "video",
  title: "Video three",
  url: "https://youtube.com/watch?v=vid3aaaaaaa",
};

const PHASES = { extract: "e1", qdrant: "q1", neo4j: "n1" };

describe("VideosTab", () => {
  it("fetches the pasted batch, pages it, and dispatches the selection", async () => {
    server.use(
      http.get(`${API}/v1/ycs/content/videos/preview`, ({ request }) => {
        const url = new URL(request.url);
        const offset = Number.parseInt(url.searchParams.get("offset") ?? "0", 10);
        return offset >= 2
          ? HttpResponse.json({
              source: "ids",
              source_id: "",
              total: 3,
              offset: 2,
              limit: 100,
              has_more: false,
              items: [V3],
            })
          : HttpResponse.json({
              source: "ids",
              source_id: "",
              total: 3,
              offset: 0,
              limit: 100,
              has_more: true,
              items: [V1, V2],
            });
      }),
      http.post(`${API}/v1/ycs/content/videos/pipeline`, () =>
        HttpResponse.json({ phases: PHASES, video_ids: ["vid1aaaaaaa", "vid2aaaaaaa", "vid3aaaaaaa"] }),
      ),
    );
    renderWith(<VideosTab initialIds={[]} />);

    await userEvent.type(
      screen.getByLabelText("Video IDs or URLs"),
      "https://youtu.be/vid1aaaaaaa, vid2aaaaaaa\nvid3aaaaaaa",
    );
    await userEvent.click(screen.getByRole("button", { name: "Fetch videos" }));

    expect(await screen.findByText("Video one")).toBeInTheDocument();
    expect(screen.getByText("Video two")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Video three")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "Select all loaded videos" }));
    expect(screen.getByText(/selected/)).toHaveTextContent("3 selected");

    await userEvent.click(screen.getByRole("button", { name: "Start Ingestion (3 videos)" }));
    await waitFor(() =>
      expect(nav.push).toHaveBeenCalledWith("/ycs/ingestion?extract=e1&qdrant=q1&neo4j=n1"),
    );

    expect(JSON.parse(localStorage.getItem("ycs:pipeline:active") ?? "{}")).toMatchObject({
      extract: "e1",
      qdrant: "q1",
      neo4j: "n1",
      video_ids: ["vid1aaaaaaa", "vid2aaaaaaa", "vid3aaaaaaa"],
    });
  });

  it("shows a readable error when the preview endpoint fails", async () => {
    server.use(
      http.get(`${API}/v1/ycs/content/videos/preview`, () =>
        HttpResponse.json({ detail: "yt-dlp subprocess failed" }, { status: 500 }),
      ),
    );
    renderWith(<VideosTab initialIds={[]} />);
    await userEvent.type(screen.getByLabelText("Video IDs or URLs"), "vid1aaaaaaa");
    await userEvent.click(screen.getByRole("button", { name: "Fetch videos" }));
    expect(await screen.findByText("yt-dlp subprocess failed")).toBeInTheDocument();
  });
});

describe("PlaylistTab", () => {
  it("unfurls the paste into a claim card, lists videos, and ingests the whole playlist", async () => {
    server.use(
      http.get(`${API}/v1/ycs/content/playlist/videos`, () =>
        HttpResponse.json({
          source: "playlist",
          source_id: "PLabc123def",
          title: "My playlist",
          channel: "Someone",
          total: 1,
          offset: 0,
          limit: 100,
          has_more: false,
          items: [
            { id: "pv1", kind: "video", title: "Playlist video", url: "https://youtube.com/watch?v=pv1" },
          ],
        }),
      ),
      http.post(`${API}/v1/ycs/content/playlist/pipeline`, () =>
        HttpResponse.json({ phases: PHASES, video_ids: ["pv1"] }),
      ),
    );
    renderWith(<PlaylistTab initialIdentity={null} routedWarn={null} />);

    const input = screen.getByLabelText("Playlist");
    await userEvent.type(input, "https://www.youtube.com/playlist?list=PLabc123def");
    // the claim card replaces the input only after the paste is committed
    expect(screen.getByLabelText("Playlist")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Fetch videos" }));
    expect(await screen.findByText("My playlist")).toBeInTheDocument();
    expect(screen.getByText("Playlist video")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Ingest all 1" }));
    await waitFor(() =>
      expect(nav.push).toHaveBeenCalledWith("/ycs/ingestion?extract=e1&qdrant=q1&neo4j=n1"),
    );
    expect(JSON.parse(localStorage.getItem("ycs:pipeline:active") ?? "{}")).toMatchObject({
      video_ids: ["pv1"],
    });
  });

  it("keeps a routed batch readable and warns when more than one was routed", async () => {
    renderWith(
      <PlaylistTab
        initialIdentity="PLfirst"
        routedWarn="Routed 2 — one Playlist at a time: the first is kept, the rest dropped."
      />,
    );
    expect(
      screen.getByText("Routed 2 — one Playlist at a time: the first is kept, the rest dropped."),
    ).toBeInTheDocument();
    expect(screen.getByText("PLfirst")).toBeInTheDocument();
  });
});
