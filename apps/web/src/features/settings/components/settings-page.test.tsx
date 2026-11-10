import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { SettingsPage } from "./settings-page";

const ORIGIN = "http://nexus.test";
const LLM_VIEW = { url: "http://g:8000/v1", model: "fgts-va", has_key: false, source: null, last4: null };
const EMBEDDING_VIEW = { ...LLM_VIEW, model: "auto" };

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(`${ORIGIN}/api/v1/settings/endpoint`, () => HttpResponse.json(LLM_VIEW)),
    http.get(`${ORIGIN}/api/v1/settings/embedding`, () => HttpResponse.json(EMBEDDING_VIEW)),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("SettingsPage", () => {
  it("shows the active connection view and its view selector; the page title is the app's second navbar", () => {
    render(
      <QueryProvider>
        <SettingsPage />
      </QueryProvider>,
    );

    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Settings views" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "LLM" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "LLM" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Embedding" })).not.toBeInTheDocument();
    expect(screen.getByText(/takes precedence over the deployment default/)).toBeInTheDocument();
  });
});
