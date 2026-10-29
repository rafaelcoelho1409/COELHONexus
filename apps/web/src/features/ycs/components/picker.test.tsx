import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { YcsOverlaysProvider } from "./overlays";
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
  return render(
    <QueryProvider>
      <YcsOverlaysProvider>{children}</YcsOverlaysProvider>
    </QueryProvider>,
  );
}

const noSwitch = () => {};

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
    renderWith(<VideosTab initialIds={[]} onSwitch={noSwitch} />);

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
    renderWith(<VideosTab initialIds={[]} onSwitch={noSwitch} />);
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
    renderWith(<PlaylistTab initialIdentity={null} routedWarn={null} onSwitch={noSwitch} />);

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
        onSwitch={noSwitch}
      />,
    );
    expect(
      screen.getByText("Routed 2 — one Playlist at a time: the first is kept, the rest dropped."),
    ).toBeInTheDocument();
    expect(screen.getByText("PLfirst")).toBeInTheDocument();
  });

  it("suggests the right tab for a paste of another kind, and moves it there on one click", async () => {
    const onSwitch = vi.fn();
    renderWith(<PlaylistTab initialIdentity={null} routedWarn={null} onSwitch={onSwitch} />);
    await userEvent.type(screen.getByLabelText("Playlist"), "@anthropicai");
    expect(screen.getByText("This looks like a channel, not a playlist.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Switch to Channel" }));
    expect(onSwitch).toHaveBeenCalledWith("channel", "@anthropicai");
  });

  it("asks before ingesting a large playlist, naming the count, and queues nothing when declined", async () => {
    const queued = vi.fn();
    server.use(
      http.get(`${API}/v1/ycs/content/playlist/videos`, () =>
        HttpResponse.json({
          source: "playlist",
          source_id: "PLabc123def",
          title: "Big playlist",
          channel: "Someone",
          total: 120,
          offset: 0,
          limit: 100,
          has_more: true,
          items: [
            { id: "pv1", kind: "video", title: "Playlist video", url: "https://youtube.com/watch?v=pv1" },
          ],
        }),
      ),
      http.post(`${API}/v1/ycs/content/playlist/pipeline`, () => {
        queued();
        return HttpResponse.json({ phases: PHASES, video_ids: ["pv1"] });
      }),
    );
    renderWith(<PlaylistTab initialIdentity={null} routedWarn={null} onSwitch={noSwitch} />);
    await userEvent.type(screen.getByLabelText("Playlist"), "PLabc123defghij");
    await userEvent.click(screen.getByRole("button", { name: "Fetch videos" }));
    await screen.findByText("Big playlist");

    await userEvent.click(screen.getByRole("button", { name: "Ingest all 120" }));
    const dialog = await screen.findByRole("dialog", { name: "Ingest all 120 videos?" });
    expect(dialog).toHaveTextContent("Queues every video of “Big playlist”");
    expect(dialog).toHaveTextContent("Transcripts on · any language");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(queued).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Ingest all 120" }));
    await userEvent.click(await screen.findByRole("button", { name: "Ingest 120 videos" }));
    await waitFor(() => expect(queued).toHaveBeenCalledTimes(1));
  });
});

describe("The Videos paste box", () => {
  const ID_A = "aaaaaaaaaaa";
  const ID_B = "bbbbbbbbbbb";

  it("says live how many videos were understood, how many repeated and which lines were not recognized", async () => {
    renderWith(<VideosTab initialIds={[]} onSwitch={noSwitch} />);
    const box = screen.getByLabelText("Video IDs or URLs");
    expect(screen.getByText(/One ID or URL per line/)).toBeInTheDocument();

    await userEvent.type(
      box,
      `${ID_A}{enter}https://youtu.be/${ID_A}{enter}${ID_B}{enter}not a video{enter}`,
    );
    expect(screen.getByText("2 videos · 1 duplicate skipped · 1 not recognized")).toBeInTheDocument();
    expect(box).toHaveAttribute("aria-invalid", "true");
    await userEvent.click(screen.getByText("Show the 1 line that were not recognized"));
    expect(screen.getByText("Line 4: not a video")).toBeInTheDocument();
  });

  it("has no ingest bar before anything is fetched", () => {
    renderWith(<VideosTab initialIds={[]} onSwitch={noSwitch} />);
    expect(screen.queryByRole("region", { name: "Ingestion" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start Ingestion/ })).not.toBeInTheDocument();
  });

  it("appends the lines of an uploaded .txt file, and refuses another kind of file", async () => {
    renderWith(<VideosTab initialIds={[]} onSwitch={noSwitch} />);
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    if (!input) throw new Error("no file input");
    await userEvent.upload(input, new File([`${ID_A}\n${ID_B}\n`], "ids.txt", { type: "text/plain" }), {
      applyAccept: false,
    });
    expect(await screen.findByText("2 videos")).toBeInTheDocument();

    await userEvent.upload(input, new File(["x"], "notes.pdf", { type: "application/pdf" }), {
      applyAccept: false,
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Drop or choose a .txt or .csv file.");
  });

  it("suggests the Playlist tab when a playlist link is pasted", async () => {
    const onSwitch = vi.fn();
    renderWith(<VideosTab initialIds={[]} onSwitch={onSwitch} />);
    await userEvent.click(screen.getByLabelText("Video IDs or URLs"));
    await userEvent.paste("https://www.youtube.com/playlist?list=PLabc123defghij");
    await userEvent.click(screen.getByRole("button", { name: "Switch to Playlist" }));
    expect(onSwitch).toHaveBeenCalledWith(
      "playlist",
      "https://www.youtube.com/playlist?list=PLabc123defghij",
    );
  });

  it("starts with the master checkbox unchecked and not mixed, selects a Shift range, and ticks the master when all are", async () => {
    const items = Array.from({ length: 4 }, (_, i) => ({
      id: `vid${i}aaaaaaa`,
      kind: "video",
      title: `Clip ${i}`,
      url: `https://youtube.com/watch?v=vid${i}aaaaaaa`,
    }));
    server.use(
      http.get(`${API}/v1/ycs/content/videos/preview`, () =>
        HttpResponse.json({
          source: "ids",
          source_id: "",
          total: 4,
          offset: 0,
          limit: 100,
          has_more: false,
          items,
        }),
      ),
    );
    renderWith(<VideosTab initialIds={[]} onSwitch={noSwitch} />);
    await userEvent.type(screen.getByLabelText("Video IDs or URLs"), items.map((i) => i.id).join("{enter}"));
    await userEvent.click(screen.getByRole("button", { name: "Fetch videos" }));
    await screen.findByText("Clip 0");

    const master = screen.getByRole("checkbox", { name: "Select all loaded videos" });
    expect(master).not.toBeChecked();
    expect(master).toHaveProperty("indeterminate", false); // nothing selected is not "some"

    const user = userEvent.setup(); // one session, so the held Shift key reaches the click
    await user.click(screen.getByRole("checkbox", { name: "Select Clip 0" }));
    expect(master).toHaveProperty("indeterminate", true);
    await user.keyboard("{Shift>}");
    await user.click(screen.getByRole("checkbox", { name: "Select Clip 3" }));
    await user.keyboard("{/Shift}");
    expect(screen.getByText(/selected/)).toHaveTextContent("4 selected");
    expect(master).toBeChecked();
    expect(screen.getByRole("button", { name: "Start Ingestion (4 videos)" })).toBeEnabled();
  });
});
