import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { SettingsPage } from "./settings-page";

const ORIGIN = "http://nexus.test";
const VIEW = { url: "http://g:8000/v1", model: "auto", has_key: false, source: null, last4: null };

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(`${ORIGIN}/api/v1/settings/endpoint`, () => HttpResponse.json(VIEW)),
    http.get(`${ORIGIN}/api/v1/settings/embedding`, () => HttpResponse.json(VIEW)),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("SettingsPage", () => {
  it("titles the page and gives each card its own intro and landmark", () => {
    render(
      <QueryProvider>
        <SettingsPage />
      </QueryProvider>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "LLM Endpoint" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Embedding" })).toBeInTheDocument();
    expect(screen.getByText(/this configuration wins over the deployment default/)).toBeInTheDocument();
    expect(screen.getByText(/separate from the LLM endpoint/)).toBeInTheDocument();
  });
});
