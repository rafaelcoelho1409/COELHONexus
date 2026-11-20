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
      "YouTube channels, searchable. Books, distilled.",
    );
    // The page is a stack of snapping sections; there is no in-page navbar.
    expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
    for (const name of [
      "Two tools, one platform.",
      "Six layers, each replaceable on its own.",
      "How it is built.",
      "Run it yourself.",
    ]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("main")).toHaveClass("snap-page");
  });

  it("links into the active apps from the header and the product card", () => {
    server.use(http.all(`${ORIGIN}/api/*`, () => HttpResponse.error()));
    renderPage();

    const console = within(screen.getByRole("navigation", { name: "Console" }));
    expect(console.getByRole("link", { name: "YouTube Content Search" })).toHaveAttribute("href", "/ycs");
    expect(console.getByRole("link", { name: "Book Distiller" })).toHaveAttribute("href", "/bd");
    expect(console.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("link", { name: "Open YouTube Search" })).toHaveAttribute("href", "/ycs");
  });

  it("shows live figures from the API, not hard-coded ones", async () => {
    server.use(
      http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ status: "healthy" })),
      http.get(`${ORIGIN}/api/v1/ycs/admin/videos`, () => HttpResponse.json({ items: [], total: 1204 })),
    );
    renderPage();

    expect(await within(platformPanel()).findByText("API operational")).toBeInTheDocument();
    const panel = within(platformPanel());
    expect(await panel.findByText("1,204")).toBeInTheDocument();
  });

  it("degrades to dashes and says so when the API is down", async () => {
    server.use(http.all(`${ORIGIN}/api/*`, () => new HttpResponse(null, { status: 503 })));
    renderPage();

    expect(await within(platformPanel()).findByText("API unreachable")).toBeInTheDocument();
    expect(within(platformPanel()).getByText("Indexed videos").nextElementSibling).toHaveTextContent("—");
  });

  it("does not trust a payload that has the wrong shape", async () => {
    server.use(
      http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ status: "degraded" })),
      http.get(`${ORIGIN}/api/v1/ycs/admin/videos`, () => HttpResponse.json({ items: [] })),
    );
    renderPage();

    expect(await within(platformPanel()).findByText("API unreachable")).toBeInTheDocument();
    expect(within(platformPanel()).getByText("Indexed videos").nextElementSibling).toHaveTextContent("—");
  });
});
