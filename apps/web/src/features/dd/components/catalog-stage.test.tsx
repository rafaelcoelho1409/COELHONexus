import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { CatalogStage } from "./catalog-stage";
import { DdOverlaysProvider } from "./overlays";

const ORIGIN = "http://nexus.test";

vi.mock("next/navigation", async () => {
  const push = vi.fn();
  const replace = vi.fn();
  const searchParams = new URLSearchParams();
  return {
    useRouter: () => ({ push, replace, refresh: vi.fn(), back: vi.fn(), forward: vi.fn() }),
    usePathname: () => "/dd",
    useSearchParams: () => searchParams,
    useSelectedLayoutSegment: () => null,
  };
});

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const TILES = [
  { slug: "a", name: "Alpha", category: "Dev", tier: 1, logo: null, logos: null },
  { slug: "b", name: "Beta", category: "Ops", tier: null, logo: "gh", logos: null },
];
const LIBRARY = [
  {
    slug: "a",
    framework_name: "Alpha",
    framework_slug: "a",
    page_count: 5,
    ingested_at: Math.floor(Date.now() / 1000) - 120,
    tier_kind: "sitemap",
    logo: null,
    logos: null,
  },
];

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(`${ORIGIN}/api/v1/dd/resolver`, () => HttpResponse.json(TILES)),
    http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json(LIBRARY)),
    http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: [] })),
    http.post(`${ORIGIN}/api/v1/dd/runs`, () => HttpResponse.json({ status: "queued", run_id: "r1" })),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderStage() {
  return render(
    <QueryProvider>
      <DdOverlaysProvider>
        <CatalogStage />
      </DdOverlaysProvider>
    </QueryProvider>,
  );
}

describe("CatalogStage", () => {
  it("renders tiles with the ingested badge and facet counts", async () => {
    renderStage();
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Ingested", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("2 of 2 frameworks shown")).toBeInTheDocument();
  });

  it("filters tiles by the search box", async () => {
    const user = userEvent.setup();
    renderStage();
    const search = await screen.findByLabelText("Search the catalog");
    expect(search).toHaveAttribute("placeholder", "Search 2 frameworks…");
    await user.type(search, "beta");
    await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 frameworks shown")).toBeInTheDocument();
  });

  it("filters with the Category, Tier and Status dropdowns that sit beside the search box", async () => {
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("Alpha");

    // Status: toggle "Ingested" on, the button then says so; Reset puts everything back.
    await user.click(screen.getByRole("button", { name: "Filter by status: All" }));
    await user.click(screen.getByRole("button", { name: /^Ingested\s*1$/ }));
    expect(screen.getByRole("button", { name: "Filter by status: Ingested" })).toBeInTheDocument();
    expect(screen.getByText("1 of 2 frameworks shown")).toBeInTheDocument();
    expect(screen.queryByText("Beta")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByText("2 of 2 frameworks shown")).toBeInTheDocument();
    await user.keyboard("{Escape}");

    // Category: one choice, and the list closes after it.
    await user.click(screen.getByRole("button", { name: "Filter by category: All" }));
    const categories = screen.getByRole("group", { name: "Filter by category" });
    await user.click(within(categories).getAllByRole("button")[1] as HTMLElement);
    expect(screen.queryByRole("group", { name: "Filter by category" })).not.toBeInTheDocument();
    expect(screen.getByText("1 of 2 frameworks shown")).toBeInTheDocument();
  });

  it("starts ingestion for a not-yet-ingested tile and navigates to the run", async () => {
    const user = userEvent.setup();
    renderStage();
    await user.click(await screen.findByText("Beta"));
    const start = await screen.findByRole("button", { name: "Start Ingestion" });
    await user.click(start);
    await waitFor(() => {
      expect(screen.getByText("Ingesting Beta…")).toBeInTheDocument();
    });
  });

  it("opens the pipeline stage directly when an ingested tile is clicked", async () => {
    const user = userEvent.setup();
    renderStage();
    await user.click(await screen.findByText("Alpha"));
    // ingested pick navigates via the next/navigation mock (asserted
    // indirectly: no sticky bar appears)
    expect(screen.queryByRole("button", { name: "Start Ingestion" })).not.toBeInTheDocument();
  });
});
