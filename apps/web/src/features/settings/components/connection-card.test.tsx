import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import type { ConnectionView } from "../entities";
import { ConnectionCard } from "./connection-card";

const ORIGIN = "http://nexus.test";
const LLM_URL = `${ORIGIN}/api/v1/settings/endpoint`;
const EMBEDDING_URL = `${ORIGIN}/api/v1/settings/embedding`;

const LLM_VIEW: ConnectionView = {
  url: "http://llm-gateway:8000/v1",
  model: "auto",
  has_key: false,
  source: null,
  last4: null,
};
const EMBEDDING_VIEW: ConnectionView = {
  url: "http://emb-gateway:8000/v1",
  model: "e5-v2",
  has_key: true,
  source: "env",
  last4: "ef01",
};

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(LLM_URL, () => HttpResponse.json(LLM_VIEW)),
    http.get(EMBEDDING_URL, () => HttpResponse.json(EMBEDDING_VIEW)),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderCards() {
  return render(
    <QueryProvider>
      <ConnectionCard kind="llm" title="LLM Endpoint" intro="LLM intro." />
      <ConnectionCard kind="embedding" title="Embedding" intro="Embedding intro." />
    </QueryProvider>,
  );
}

describe("ConnectionCard", () => {
  it("shows Loading… while the query is in flight, then the loaded fields", async () => {
    server.use(
      http.get(LLM_URL, async () => {
        await new Promise((resolve) => setTimeout(resolve, 40));
        return HttpResponse.json(LLM_VIEW);
      }),
    );
    render(
      <QueryProvider>
        <ConnectionCard kind="llm" title="LLM Endpoint" intro="LLM intro." />
      </QueryProvider>,
    );

    const card = screen.getByRole("region", { name: "LLM Endpoint" });
    expect(within(card).getByText("Loading…")).toBeInTheDocument();
    expect(await within(card).findByLabelText("Base URL")).toHaveValue("http://llm-gateway:8000/v1");
    expect(within(card).queryByText("Loading…")).not.toBeInTheDocument();
    expect(within(card).getByLabelText("API key")).toHaveValue("");
  });

  it("says so when the load fails and lets you retry", async () => {
    server.use(http.get(LLM_URL, () => HttpResponse.error()));
    render(
      <QueryProvider>
        <ConnectionCard kind="llm" title="LLM Endpoint" intro="LLM intro." />
      </QueryProvider>,
    );

    expect(await screen.findByText(/Couldn't load the saved settings/)).toBeInTheDocument();
    server.use(http.get(LLM_URL, () => HttpResponse.json(LLM_VIEW)));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Base URL")).toHaveValue("http://llm-gateway:8000/v1");
  });

  it("the two cards hit different URLs and keep their data apart", async () => {
    renderCards();

    const llmCard = await screen.findByRole("region", { name: "LLM Endpoint" });
    expect(await within(llmCard).findByLabelText("Base URL")).toHaveValue("http://llm-gateway:8000/v1");
    const embeddingCard = screen.getByRole("region", { name: "Embedding" });
    expect(within(embeddingCard).getByLabelText("Base URL")).toHaveValue("http://emb-gateway:8000/v1");
    expect(within(embeddingCard).getByLabelText("Model")).toHaveValue("e5-v2");
    expect(within(embeddingCard).getByText("Env key ••••ef01")).toBeInTheDocument();
  });

  it("detects a payload that does not parse", async () => {
    server.use(http.get(EMBEDDING_URL, () => HttpResponse.json({ url: 3 })));
    renderCards();

    const card = await screen.findByRole("region", { name: "Embedding" });
    expect(await within(card).findByText(/Couldn't load the saved settings/)).toBeInTheDocument();
  });
});
