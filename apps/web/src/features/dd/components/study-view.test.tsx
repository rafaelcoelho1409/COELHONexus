import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { StudyView } from "./study-view";

const nav = vi.hoisted(() => ({ push: vi.fn(), params: new URLSearchParams("slug=alpha&chapter=ch-1") }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn(), refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => "/dd/study",
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const API = "*/api/v1/dd";

const CHAPTERS = {
  study_total_wall_ms: 65_000,
  chapters: [
    {
      id: "ch-1",
      title: "Hallucinations",
      order: 1,
      rendered: true,
      wall_ms: 30_000,
      thread_id: "synth/alpha/1",
    },
    {
      id: "ch-2",
      title: "Evaluation",
      order: 2,
      rendered: true,
      wall_ms: 35_000,
      thread_id: "synth/alpha/2",
    },
    { id: "ch-3", title: "Deployment", order: 3, rendered: false, wall_ms: null, thread_id: null },
  ],
};

const CHAPTER_ONE = [
  "# Hallucinations",
  "",
  "## Contents",
  "",
  "- [Verifiers](#verifiers)",
  "- [Retrieval](#retrieval)",
  "",
  "---",
  "",
  "## Verifiers",
  "",
  "Verifiers cut hallucination rates before collapse.",
  "",
  "Sources for this section:",
  "",
  "- `0000-intro.md` — Intro",
  "- `0099-gone.md` — Removed page",
  "",
  "## Retrieval",
  "",
  "```js",
  "const grounded = true;",
  "```",
].join("\n");

const CHAPTER_TWO = "# Evaluation\n\n## Judges\n\nMeasure rates with a judge.\n";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: "http://nexus.test" });
  nav.push.mockClear();
  nav.params = new URLSearchParams("slug=alpha&chapter=ch-1");
  server.use(
    http.get(`${API}/synth/alpha/study/chapters`, () => HttpResponse.json(CHAPTERS)),
    http.get(`${API}/synth/alpha/study/ch-1/artifact/README.md`, () => HttpResponse.text(CHAPTER_ONE)),
    http.get(`${API}/synth/alpha/study/ch-2/artifact/README.md`, () => HttpResponse.text(CHAPTER_TWO)),
    http.get(`${API}/ingestion/alpha/manifest`, () =>
      HttpResponse.json({
        framework_name: "Alpha",
        entries: [
          {
            idx: 0,
            key: "ingest/alpha/pages/0000-intro.md",
            title: "Intro",
            slug: "intro",
            tier: "docs",
            bytes: 2048,
          },
          {
            idx: 1,
            key: "ingest/alpha/pages/0001-setup.md",
            title: "Setup",
            slug: "setup",
            tier: "docs",
            bytes: 1024,
          },
        ],
      }),
    ),
    http.get(`${API}/ingestion/alpha/pages/:idx`, ({ params }) =>
      HttpResponse.json({ body: params.idx === "0" ? "# Intro\n\nIntro body." : "# Setup\n\nSetup body." }),
    ),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const renderStudy = () =>
  render(
    <QueryProvider>
      <StudyView slug="alpha" />
    </QueryProvider>,
  );

describe("Study reader", () => {
  it("renders the chapter without its inline Contents, with an outline and the chapter list", async () => {
    renderStudy();
    expect(await screen.findByText("Verifiers cut hallucination rates before collapse.")).toBeInTheDocument();
    expect(screen.getByTestId("study-title")).toHaveTextContent("Hallucinations");
    expect(screen.queryByRole("heading", { name: "Contents" })).not.toBeInTheDocument();

    const outline = screen.getByRole("navigation", { name: "On this page" });
    expect(
      within(outline)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href")),
    ).toEqual(["#verifiers", "#retrieval"]);

    const list = screen.getByRole("navigation", { name: "Chapters" });
    expect(within(list).getByText("1m 5s")).toBeInTheDocument(); // the whole study's run time
    expect(within(list).getByText("30.0 s")).toBeInTheDocument(); // one chapter's
    expect(within(list).getByRole("link", { name: /Hallucinations/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(list).getByText("pending")).toBeInTheDocument();
  });

  it("highlights code and folds the sources of a section into a collapsed block", async () => {
    renderStudy();
    expect(await screen.findByText("const")).toHaveClass("hljs-keyword");
    const summary = screen.getByText("Sources for this section (2)");
    expect(summary.tagName).toBe("SUMMARY");
    expect(summary.closest("details")).not.toHaveAttribute("open");
  });

  it("opens the cited page in a sheet that steps through the library", async () => {
    renderStudy();
    await userEvent.click(await screen.findByText("Sources for this section (2)"));
    await userEvent.click(screen.getByRole("button", { name: "0000-intro.md" }));

    const sheet = await screen.findByRole("dialog", { name: "Intro" });
    expect(await within(sheet).findByText("Intro body.")).toBeInTheDocument();
    expect(within(sheet).getByText(/1 of 2/)).toBeInTheDocument();

    await userEvent.click(within(sheet).getByRole("button", { name: /Next/ }));
    expect(await screen.findByRole("dialog", { name: "Setup" })).toBeInTheDocument();
    expect(await screen.findByText("Setup body.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close page" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("says so when a cited page is no longer in the library", async () => {
    renderStudy();
    await userEvent.click(await screen.findByText("Sources for this section (2)"));
    await userEvent.click(screen.getByRole("button", { name: "0099-gone.md" }));
    expect(await screen.findByText(/not in the ingested library any more/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("searches every rendered chapter with Ctrl+K and jumps to the heading it found", async () => {
    renderStudy();
    await screen.findByText("Verifiers cut hallucination rates before collapse.");
    await userEvent.keyboard("{Control>}k{/Control}");

    const search = await screen.findByRole("dialog", { name: "Search all chapters" });
    const field = within(search).getByRole("combobox", { name: "Search all chapters" });
    await userEvent.type(field, "retrieval");
    const option = await within(search).findByRole("option", { name: /Retrieval/ });
    expect(option).toHaveAttribute("aria-selected", "true");

    await userEvent.keyboard("{Enter}");
    expect(nav.push).toHaveBeenCalledWith("/dd/study?slug=alpha&chapter=ch-1#retrieval");
  });

  it("finds a match in another chapter's text and opens that chapter", async () => {
    renderStudy();
    await screen.findByText("Verifiers cut hallucination rates before collapse.");
    await userEvent.click(screen.getByRole("button", { name: /Search chapters/ }));
    const search = await screen.findByRole("dialog", { name: "Search all chapters" });
    await userEvent.type(within(search).getByRole("combobox"), "judge");
    await userEvent.click(await within(search).findByRole("option", { name: /Evaluation/ }));
    expect(nav.push).toHaveBeenCalledWith("/dd/study?slug=alpha&chapter=ch-2#judges");
  });

  it("tells the reader a pending chapter has nothing to read yet", async () => {
    nav.params = new URLSearchParams("slug=alpha&chapter=ch-3");
    renderStudy();
    expect(await screen.findByText(/pending synthesis/)).toBeInTheDocument();
  });

  it("says so when the chapter cannot be loaded", async () => {
    server.use(
      http.get(
        `${API}/synth/alpha/study/ch-1/artifact/README.md`,
        () => new HttpResponse(null, { status: 404 }),
      ),
    );
    renderStudy();
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  });
});
