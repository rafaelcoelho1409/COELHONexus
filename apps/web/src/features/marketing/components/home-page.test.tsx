import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { HomePage } from "./home-page";

const ORIGIN = "http://nexus.test";
const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => client.setConfig({ baseUrl: ORIGIN }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  return render(
    <QueryProvider>
      <HomePage />
    </QueryProvider>,
  );
}

function platformPanel() {
  return screen.getByRole("region", { name: "Platform" });
}

describe("HomePage", () => {
  it("states what the products do and offers real navigation", () => {
    server.use(http.all(`${ORIGIN}/api/*`, () => HttpResponse.error()));
    renderPage();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Framework docs, distilled. YouTube channels, searchable.",
    );
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    for (const name of ["Products", "System", "Principles"]) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", `#${name.toLowerCase()}`);
    }
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
  });

  it("links into the two apps from the header, the hero and the product card", () => {
    server.use(http.all(`${ORIGIN}/api/*`, () => HttpResponse.error()));
    renderPage();

    const console = within(screen.getByRole("navigation", { name: "Console" }));
    expect(console.getByRole("link", { name: "Docs Distiller" })).toHaveAttribute("href", "/dd");
    expect(console.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getAllByRole("link", { name: "Open Docs Distiller" })).toHaveLength(2); // hero + card
    for (const link of screen.getAllByRole("link", { name: "Open Docs Distiller" })) {
      expect(link).toHaveAttribute("href", "/dd");
    }
  });

  it("shows live figures from the API, not hard-coded ones", async () => {
    server.use(
      http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ status: "healthy" })),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json([{ slug: "a" }, { slug: "b" }])),
      http.get(`${ORIGIN}/api/v1/ycs/admin/videos`, () => HttpResponse.json({ items: [], total: 1204 })),
      http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ active: [{ id: "run-1" }] })),
    );
    renderPage();

    expect(await within(platformPanel()).findByText("API operational")).toBeInTheDocument();
    const panel = within(platformPanel());
    expect(await panel.findByText("1,204")).toBeInTheDocument();
    expect(panel.getByText("Documentation sets").nextElementSibling).toHaveTextContent("2");
    expect(panel.getByText("Runs in progress").nextElementSibling).toHaveTextContent("1");
  });

  it("degrades to dashes and says so when the API is down", async () => {
    server.use(http.all(`${ORIGIN}/api/*`, () => new HttpResponse(null, { status: 503 })));
    renderPage();

    expect(await within(platformPanel()).findByText("API unreachable")).toBeInTheDocument();
    for (const label of ["Documentation sets", "Indexed videos", "Runs in progress"]) {
      expect(within(platformPanel()).getByText(label).nextElementSibling).toHaveTextContent("—");
    }
  });

  it("does not trust a payload that has the wrong shape", async () => {
    server.use(
      http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ status: "degraded" })),
      http.get(`${ORIGIN}/api/v1/dd/ingestion`, () => HttpResponse.json({ not: "a list" })),
      http.get(`${ORIGIN}/api/v1/ycs/admin/videos`, () => HttpResponse.json({ items: [] })),
      http.get(`${ORIGIN}/api/v1/dd/runs/active`, () => HttpResponse.json({ runs: [] })),
    );
    renderPage();

    expect(await within(platformPanel()).findByText("API unreachable")).toBeInTheDocument();
    expect(within(platformPanel()).getByText("Indexed videos").nextElementSibling).toHaveTextContent("—");
  });
});
