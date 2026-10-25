import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import type { ConnectionView } from "../entities";
import { useConnection } from "../hooks/use-connection";
import type { ConnectionKind } from "../params";
import { ConnectionForm } from "./connection-form";

const ORIGIN = "http://nexus.test";
const LLM_URL = `${ORIGIN}/api/v1/settings/endpoint`;
const EMBEDDING_URL = `${ORIGIN}/api/v1/settings/embedding`;
const LLM_TEST_URL = `${ORIGIN}/api/v1/settings/endpoint/test`;
const EMBEDDING_TEST_URL = `${ORIGIN}/api/v1/settings/embedding/test`;

const VIEW: ConnectionView = {
  url: "http://gateway.internal:8000/v1",
  model: "m1",
  has_key: true,
  source: "user",
  last4: "abcd",
};

const SAVED: ConnectionView = { ...VIEW, has_key: true, source: "env", last4: "ef02" };

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(LLM_URL, () => HttpResponse.json(VIEW)),
    http.get(EMBEDDING_URL, () => HttpResponse.json(VIEW)),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function FormHarness({ kind, initial }: { kind: ConnectionKind; initial: ConnectionView }) {
  return (
    <QueryProvider>
      <WiredForm kind={kind} initial={initial} />
    </QueryProvider>
  );
}

function WiredForm({ kind, initial }: { kind: ConnectionKind; initial: ConnectionView }) {
  const { save, test } = useConnection(kind);
  return <ConnectionForm kind={kind} initial={initial} save={save} test={test} />;
}

describe("ConnectionForm", () => {
  it("loads the fields from the saved view and lets them be edited", async () => {
    render(<FormHarness kind="llm" initial={VIEW} />);
    // The hook inside the harness loads the view first; the same one is then mirrored into the form.
    expect(await screen.findByLabelText("Base URL")).toHaveValue("http://gateway.internal:8000/v1");
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(screen.getByLabelText("Model")).toHaveValue("m1");

    await userEvent.type(screen.getByLabelText("Base URL"), "/extra");
    expect(screen.getByLabelText("Base URL")).toHaveValue("http://gateway.internal:8000/v1/extra");
  });

  it("saves a typed key exactly once, in the body, and clears the field afterwards", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(LLM_URL, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(SAVED);
      }),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.type(await screen.findByLabelText("API key"), "sk-secret-1");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({
      url: "http://gateway.internal:8000/v1",
      model: "m1",
      api_key: "sk-secret-1",
    });
    expect(screen.getByLabelText("API key")).toHaveValue("");
  });

  it("omits api_key entirely when the field is untouched, so the stored key survives", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(EMBEDDING_URL, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(SAVED);
      }),
    );
    render(<FormHarness kind="embedding" initial={{ ...VIEW, has_key: false, source: null, last4: null }} />);

    await screen.findByLabelText("Base URL");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({ url: "http://gateway.internal:8000/v1", model: "m1" });
    expect(Object.keys(bodies[0] as object)).not.toContain("api_key");
  });

  it("Remove key sends an empty api_key to clear the stored key", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(LLM_URL, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...VIEW, has_key: false, source: null, last4: null });
      }),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.click(await screen.findByRole("button", { name: "Remove key" }));

    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({ url: "http://gateway.internal:8000/v1", model: "m1", api_key: "" });
  });

  it("an invalid base URL blocks the request and shows the error under the field", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(LLM_URL, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(VIEW);
      }),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.clear(screen.getByLabelText("Base URL"));
    await userEvent.type(screen.getByLabelText("Base URL"), "not a url");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(bodies).toHaveLength(0);
    expect(screen.getByText(/Enter a URL that starts with http/)).toBeInTheDocument();
    expect(screen.getByLabelText("Base URL")).toHaveAttribute("aria-invalid", "true");
  });

  it("an invalid base URL also blocks Remove key", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(LLM_URL, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(VIEW);
      }),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.clear(screen.getByLabelText("Base URL"));
    await userEvent.click(screen.getByRole("button", { name: "Remove key" }));

    expect(bodies).toHaveLength(0);
    expect(screen.getByText(/Enter a URL that starts with http/)).toBeInTheDocument();
  });

  it("falls back to the default model when the field is blank", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(LLM_URL, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(VIEW);
      }),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.clear(screen.getByLabelText("Model"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({ url: "http://gateway.internal:8000/v1", model: "auto" });
  });

  it("surfaces a 408-free detail string verbatim", async () => {
    server.use(
      http.put(LLM_URL, () =>
        HttpResponse.json({ detail: "Key is managed by the deployment" }, { status: 400 }),
      ),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Key is managed by the deployment")).toBeInTheDocument();
    expect(screen.queryByText("Saved.")).not.toBeInTheDocument();
  });

  it("a 422 list shows the first msg", async () => {
    server.use(
      http.put(LLM_URL, () =>
        HttpResponse.json(
          { detail: [{ loc: [], msg: "url is required", type: "value_error" }] },
          { status: 422 },
        ),
      ),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("url is required")).toBeInTheDocument();
  });

  it("shows the test result — ok, with latency, and failure", async () => {
    server.use(
      http.post(LLM_TEST_URL, () =>
        HttpResponse.json({ ok: true, reply: "pict", latency_ms: 412, model: "model-id" }),
      ),
    );
    render(<FormHarness kind="llm" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.click(screen.getByRole("button", { name: "Test" }));

    expect(await screen.findByText("OK — 412 ms · model-id")).toBeInTheDocument();
  });

  it("shows a failed test and keeps the saved note about unsaved edits", async () => {
    server.use(
      http.post(EMBEDDING_TEST_URL, () =>
        HttpResponse.json({ ok: false, error: "Connection refused", latency_ms: 3 }),
      ),
    );
    render(<FormHarness kind="embedding" initial={VIEW} />);

    await screen.findByLabelText("Base URL");
    await userEvent.clear(screen.getByLabelText("Base URL"));
    await userEvent.click(screen.getByRole("button", { name: "Test" }));

    expect(await screen.findByText("Failed: Connection refused")).toBeInTheDocument();
    expect(screen.getByText("Unsaved changes — Test uses the saved settings.")).toBeInTheDocument();
  });
});
