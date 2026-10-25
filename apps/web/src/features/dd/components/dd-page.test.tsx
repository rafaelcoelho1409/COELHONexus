import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { DocsDistillerPage } from "./dd-page";

const ORIGIN = "http://nexus.test";

// Module-level mutable nav config per test scenario.
const routerState = { pathname: "/dd", search: "" as string };

vi.mock("next/navigation", async () => {
  const replace = vi.fn();
  const push = vi.fn();
  return {
    useRouter: () => ({ push, replace, refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
    usePathname: () => routerState.pathname,
    useSearchParams: () => new URLSearchParams(routerState.search),
    useSelectedLayoutSegment: () => null,
  };
});

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const LIBRARY = [
  {
    slug: "a",
    framework_name: "Alpha",
    framework_slug: "a",
    page_count: 2,
    ingested_at: Math.floor(Date.now() / 1000) - 120,
    tier_kind: "sitemap",
    logo: null,
    logos: null,
  },
];
const MANIFEST = {
  framework_name: "Alpha",
  framework_slug: "a",
  total_bytes: 640,
  tier_kind: "sitemap",
  ingested_at: Math.floor(Date.now() / 1000) - 120,
  entries: [
    {
      idx: 0,
      title: "Quickstart",
      slug: null,
      tier: "llms-full",
      bytes: 320,
      url: "https://docs.example.com/docs/quickstart",
      source_path: null,
      section: "Docs",
      notes: null,
    },
    {
      idx: 1,
      title: "CLI",
      slug: null,
      tier: "docs",
      bytes: 320,
      url: "https://docs.example.com/docs/cli",
      source_path: null,
      section: "Docs",
      notes: null,
    },
  ],
};

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json(LIBRARY)),
    http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: [] })),
    http.get(`${ORIGIN}/api/v1/dd/ingestion/a/manifest`, () => HttpResponse.json(MANIFEST)),
    http.get(`${ORIGIN}/api/v1/dd/ingestion/a/pages/0`, () => HttpResponse.json({ body: "quickstart body" })),
  );
  routerState.pathname = "/dd";
  routerState.search = "";
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  return render(
    <QueryProvider>
      <DocsDistillerPage />
    </QueryProvider>,
  );
}

describe("DocsDistillerPage", () => {
  it("shows the catalog stage with the four stage tabs", async () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "Docs Distiller" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ingestion" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pipeline" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Study" })).toBeInTheDocument();
    const catalog = screen.getByText("Catalog");
    expect(catalog).toHaveAttribute("aria-current", "page");
  });

  it("renders the ingestion explorer with groups, search shortcut and preview", async () => {
    routerState.pathname = "/dd/ingestion";
    routerState.search = "slug=a";
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("Docs")).toBeInTheDocument();
    expect(screen.getByText("Alpha · 2 pages · 640 B sitemap · 2m ago")).toBeInTheDocument();
    expect(screen.getAllByText("Quickstart").length).toBeGreaterThan(0);
    expect(screen.getByText("2 of 2 pages")).toBeInTheDocument();

    const quickstart = screen.getAllByText("Quickstart")[0];
    if (!quickstart) {
      throw new Error("Quickstart row missing");
    }
    await user.click(quickstart.closest("button") as HTMLElement);
    await waitFor(() => expect(screen.getByText("quickstart body")).toBeInTheDocument());
  });

  it("shows a picker with the current framework on non-catalog stages", async () => {
    routerState.pathname = "/dd/pipeline";
    routerState.search = "slug=a";
    renderPage();
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: /alpha/i });
    await userEvent.click(trigger);
    expect(screen.getByLabelText("Search frameworks")).toBeInTheDocument();
    expect(screen.getByLabelText("Delete Alpha")).toBeInTheDocument();
  });

  it("renders the study stage shell for frameworks", () => {
    routerState.pathname = "/dd/study";
    routerState.search = "slug=a";
    renderPage();
    expect(screen.getByText("Chapters")).toBeInTheDocument();
  });
});
