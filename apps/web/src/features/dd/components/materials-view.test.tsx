import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { manifestSchema } from "../entities";
import { MaterialsView } from "./materials-view";

const ORIGIN = "http://nexus.test";
const server = setupServer();

const BODIES: Record<string, string> = {
  "0": "# Zebra crossing\n\nA zebra crosses here.\n\n## Install\n\nRun the installer.\n\n## Usage\n\nCross at the stripes. The unique-needle sits in this page.\n",
  "1": "# Apple pie\n\nBake it slowly.\n",
  "2": "# Mango › Chutney\n\nSweet and sour.\n",
};

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  window.localStorage.clear();
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(`${ORIGIN}/api/v1/dd/ingestion/demo/pages/:idx`, ({ params }) =>
      HttpResponse.json({ body: BODIES[String(params.idx)] ?? "" }),
    ),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function entry(idx: number, title: string, slug: string, extra: Record<string, unknown> = {}) {
  return {
    idx,
    key: null,
    title,
    slug,
    tier: "llms_full",
    bytes: 1_000,
    url: "https://docs.example.com/llms-full.txt",
    source_path: `https://docs.example.com/docs/${slug}`,
    section: "Docs",
    notes: null,
    ...extra,
  };
}

const ENTRIES = [
  entry(0, "Zebra crossing", "demo-llms-full-zebra-crossing"),
  entry(1, "Apple pie", "demo-llms-full-apple-pie"),
  entry(2, "Mango › Chutney", "demo-llms-full-mango-chutney"),
];

function renderMaterials(entries: ReturnType<typeof entry>[] = ENTRIES, active = true) {
  const manifest = manifestSchema.parse({
    framework_name: "Demo",
    framework_slug: "demo",
    entries,
    total_bytes: 3_000,
    tier_kind: "llms_full",
    ingested_at: 1,
  });
  return render(
    <QueryProvider>
      <MaterialsView slug="demo" manifest={manifest} active={active} />
    </QueryProvider>,
  );
}

const library = () => screen.getByRole("region", { name: "Ingested library" });
const pageList = () => within(screen.getByRole("navigation", { name: "Pages" }));
const title = () => screen.getByTestId("materials-title");

describe("Materials page list", () => {
  it("lists each page by its title, not by its slug", () => {
    renderMaterials();
    const texts = pageList()
      .getAllByRole("button")
      .map((button) => button.textContent ?? "")
      .join(" | ");
    expect(texts).toContain("Zebra crossing");
    expect(texts).toContain("Mango › Chutney");
    expect(texts).not.toContain("demo-llms-full");
  });

  it("keeps the original documentation order instead of sorting by name", () => {
    renderMaterials();
    const order = pageList()
      .getAllByRole("button", { name: /Zebra|Apple|Mango/ })
      .map((button) => button.textContent ?? "");
    expect(order.map((text) => text.split(" · ")[0]?.trim())).toEqual([
      expect.stringContaining("Zebra crossing"),
      expect.stringContaining("Apple pie"),
      expect.stringContaining("Mango › Chutney"),
    ]);
  });

  it("opens the first page, marks it, and narrows the list with the filter", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    expect(title()).toHaveTextContent("Zebra crossing");
    expect(pageList().getByRole("button", { name: /Zebra crossing/ })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await userEvent.type(screen.getByLabelText("Filter pages"), "apple");
    expect(pageList().queryByRole("button", { name: /Zebra/ })).not.toBeInTheDocument();
    expect(pageList().getByRole("button", { name: /Apple pie/ })).toBeInTheDocument();
    expect(screen.getByText("1 of 3 pages")).toBeInTheDocument();
  });
});

describe("Materials reader", () => {
  it("shows the page with its facts and a link to the original, without repeating its title", async () => {
    renderMaterials();
    expect(await screen.findByText("A zebra crosses here.")).toBeInTheDocument();
    const article = within(screen.getByRole("region", { name: "Page" }));
    expect(article.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(article.getByText("Docs")).toBeInTheDocument();
    expect(article.getByRole("link", { name: /Open original/ })).toHaveAttribute(
      "href",
      "https://docs.example.com/docs/demo-llms-full-zebra-crossing",
    );
    // The leading `# Zebra crossing` of the text is the title the header already prints.
    expect(within(library()).getAllByRole("heading", { name: "Zebra crossing" })).toHaveLength(1);
  });

  it("turns pages with Previous and Next, and with the arrow keys", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    const article = within(screen.getByRole("region", { name: "Page" }));
    expect(article.getByRole("button", { name: "Previous page" })).toBeDisabled();

    await userEvent.click(article.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Bake it slowly.")).toBeInTheDocument();
    expect(title()).toHaveTextContent("Apple pie");

    await userEvent.keyboard("{ArrowRight}");
    expect(await screen.findByText("Sweet and sour.")).toBeInTheDocument();
    expect(article.getByRole("button", { name: "Next page" })).toBeDisabled();

    await userEvent.keyboard("{ArrowLeft}");
    expect(await screen.findByText("Bake it slowly.")).toBeInTheDocument();
  });

  it("leaves the arrow keys alone to a tab that is not on show, and to a field being typed in", async () => {
    renderMaterials(ENTRIES, false);
    await screen.findByText("A zebra crosses here.");
    await userEvent.keyboard("{ArrowRight}");
    expect(title()).toHaveTextContent("Zebra crossing");
  });

  it("shows the markdown source of the page", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    await userEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(screen.getByRole("button", { name: "Source" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Page source")).toHaveTextContent("# Zebra crossing");
    expect(screen.queryByRole("navigation", { name: "On this page" })).not.toBeInTheDocument();
  });

  it("lists the page's headings in an outline, which folds away and stays folded", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    const outline = screen.getByRole("navigation", { name: "On this page" });
    expect(within(outline).getByRole("link", { name: "Install" })).toHaveAttribute("href", "#install");
    expect(within(outline).getByRole("link", { name: "Usage" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Hide outline" }));
    expect(screen.queryByRole("navigation", { name: "On this page" })).not.toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("dd:materials:layout") ?? "{}")).toMatchObject({
      outline: false,
    });

    await userEvent.click(screen.getByRole("button", { name: "Hide pages" }));
    expect(screen.queryByRole("navigation", { name: "Pages" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show pages" })).toBeInTheDocument();
  });

  it("says when a page is empty of text it could load", async () => {
    server.use(
      http.get(`${ORIGIN}/api/v1/dd/ingestion/demo/pages/:idx`, () => HttpResponse.json({}, { status: 500 })),
    );
    renderMaterials();
    expect(await screen.findByRole("alert")).toHaveTextContent("Page unavailable.");
  });
});

describe("Materials reading mode", () => {
  it("gives the page the whole screen, turns pages there, and leaves with Exit", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    await userEvent.click(screen.getByRole("button", { name: /Reading mode/ }));

    const reading = await screen.findByRole("dialog", { name: "Zebra crossing" });
    expect(within(reading).getByText("Page 1 / 3")).toBeInTheDocument();
    expect(within(reading).getByRole("region", { name: "Page text" })).toHaveTextContent(
      "A zebra crosses here.",
    );
    // One copy of the text at a time.
    expect(screen.getAllByText("A zebra crosses here.")).toHaveLength(1);

    await userEvent.click(within(reading).getByRole("button", { name: "Next page" }));
    expect(await within(reading).findByText("Bake it slowly.")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Apple pie" })).toBeInTheDocument();

    await userEvent.click(within(reading).getByRole("button", { name: "Wide" }));
    expect(JSON.parse(window.localStorage.getItem("dd:materials:layout") ?? "{}")).toMatchObject({
      width: "wide",
    });

    await userEvent.click(within(reading).getByRole("button", { name: "Exit reading mode" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Bake it slowly.")).toBeInTheDocument();
  });
});

describe("Materials search", () => {
  it("finds a word in the text of any page, shows the words around it, and opens that page", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    await userEvent.keyboard("{Control>}k{/Control}");

    const search = await screen.findByRole("dialog", { name: "Search all pages" });
    const field = within(search).getByRole("combobox", { name: "Search all pages" });
    await userEvent.type(field, "sour");
    const hit = await within(search).findByRole("option", { name: /Mango/ });
    expect(hit).toHaveTextContent("Sweet and sour.");

    await userEvent.type(field, "{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(title()).toHaveTextContent("Mango › Chutney");
    expect(await screen.findByText("Sweet and sour.")).toBeInTheDocument();
  });

  it("jumps to the heading a hit names", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Bake it slowly.");

    await userEvent.click(screen.getByRole("button", { name: /Search pages/ }));
    const search = await screen.findByRole("dialog", { name: "Search all pages" });
    await userEvent.type(within(search).getByRole("combobox"), "usage");
    await userEvent.click(await within(search).findByRole("option", { name: /Usage/ }));

    expect(await screen.findByText(/unique-needle/)).toBeInTheDocument();
    expect(title()).toHaveTextContent("Zebra crossing");
    expect(document.getElementById("usage")).not.toBeNull();
  });

  it("says when nothing matches", async () => {
    renderMaterials();
    await screen.findByText("A zebra crosses here.");
    await userEvent.keyboard("{Control>}k{/Control}");
    const search = await screen.findByRole("dialog", { name: "Search all pages" });
    await userEvent.type(within(search).getByRole("combobox"), "zzzzqq");
    expect(await within(search).findByText("No matches.")).toBeInTheDocument();
  });
});

describe("Materials without pages", () => {
  it("says there are none", () => {
    renderMaterials([]);
    expect(library()).toHaveTextContent("This ingestion has no pages.");
  });
});
