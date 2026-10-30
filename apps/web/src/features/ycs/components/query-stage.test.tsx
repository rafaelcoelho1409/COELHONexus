import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";
import { YcsOverlaysProvider } from "./overlays";
import { QueryStage } from "./query-stage";

const ORIGIN = "http://nexus.test";
const API = "*/api/v1/ycs/query";

// CodeMirror needs real layout; the browser test (e2e) covers the real editor. Here it is a plain textarea with the
// same contract (value in, text out, Mod-Enter runs).
vi.mock("./query-editor-cm", () => ({
  QueryEditorCm: (props: {
    value: string;
    label: string;
    locked: boolean;
    onChange: (value: string) => void;
    onRun: () => void;
  }) => (
    <textarea
      aria-label={props.label}
      value={props.value}
      readOnly={props.locked}
      onChange={(event) => props.onChange(event.target.value)}
      onKeyDown={(event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") props.onRun();
      }}
    />
  ),
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    // A successful Run records itself in the history.
    http.post(`${API}/history`, () => HttpResponse.json({ id: 1 })),
    http.get(`${API}/history`, () => HttpResponse.json({ items: [], total: 0 })),
    http.get(`${API}/namespaces`, () =>
      HttpResponse.json({
        matrix: {
          ycs: {
            elasticsearch: { available: true, label: "YCS · metadata + transcripts" },
            qdrant: { available: true, label: "YCS · transcript chunks" },
            neo4j: { available: true, label: "YCS · entities + videos" },
          },
        },
      }),
    ),
  );
});
afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});
afterAll(() => server.close());

function renderStage() {
  return render(
    <QueryProvider>
      <YcsOverlaysProvider>
        <QueryStage />
      </YcsOverlaysProvider>
    </QueryProvider>,
  );
}

const ES_HITS = [
  {
    summary: "b",
    raw: {
      _index: "meta",
      _id: "2",
      _score: 1.5,
      _source: { title: "Beta talk", views: 10, tags: ["x", "y"] },
    },
  },
  {
    summary: "a",
    raw: { _index: "meta", _id: "1", _score: 3.25, _source: { title: "Alpha talk", views: 2, tags: [] } },
  },
  {
    summary: "c",
    raw: { _index: "meta", _id: "3", _score: 0.5, _source: { title: "Gamma talk", views: 33, tags: ["z"] } },
  },
];

function esOk(extra: Record<string, unknown> = {}) {
  return HttpResponse.json({
    backend: "elasticsearch",
    app: "ycs",
    ok: true,
    notes: [],
    took_ms: 12,
    total: 3,
    hits: ES_HITS,
    ...extra,
  });
}

function sse(events: unknown[]): HttpResponse<string> {
  const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  return new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });
}

async function editor() {
  return await screen.findByRole("textbox", { name: "Query editor" });
}

describe("QueryStage", () => {
  it("starts on Elasticsearch with its scaffold, caption and the empty state", async () => {
    renderStage();
    const box = await editor();
    expect((box as HTMLTextAreaElement).value).toContain("multi_match");
    expect(await screen.findByText("YCS · metadata + transcripts")).toBeInTheDocument();
    expect(screen.getByText("Query workbench")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("runs the editor text and shows a sortable, filterable table", async () => {
    const user = userEvent.setup();
    let sent: unknown = null;
    let saved: unknown = null;
    server.use(
      http.post(`${API}/raw/elasticsearch`, async ({ request }) => {
        sent = await request.json();
        return esOk();
      }),
      http.post(`${API}/history`, async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ id: 1 });
      }),
    );
    renderStage();
    await editor();
    await user.click(screen.getByRole("button", { name: /^Run/ }));

    expect(await screen.findByText("Done · 12 ms")).toBeInTheDocument();
    expect(sent).toMatchObject({ app: "ycs", body: expect.stringContaining("multi_match") });
    await waitFor(() => expect(saved).toMatchObject({ backend: "elasticsearch", app: "ycs" }));
    expect(screen.getByText(/3 of 3 rows · 12 ms/)).toBeInTheDocument();

    // Columns read score first, the document's fields next, identifiers last; `_index` is the same on every hit, so
    // it starts hidden: _score, title, views, tags, _id.
    const titles = () =>
      [...screen.getByRole("table").querySelectorAll("tbody tr")].map(
        (row) => within(row as HTMLElement).getAllByRole("cell")[1]?.textContent,
      );
    expect(
      within(screen.getByRole("table"))
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["#", "_score", "title", "views", "tags", "_id"]);
    expect(titles()).toEqual(["Beta talk", "Alpha talk", "Gamma talk"]);

    await user.click(screen.getByRole("button", { name: /^views/ }));
    expect(titles()).toEqual(["Alpha talk", "Beta talk", "Gamma talk"]);
    expect(screen.getByRole("columnheader", { name: /views/ })).toHaveAttribute("aria-sort", "ascending");
    await user.click(screen.getByRole("button", { name: /^views/ }));
    expect(titles()).toEqual(["Gamma talk", "Beta talk", "Alpha talk"]);

    await user.click(screen.getByRole("button", { name: "Filter columns" }));
    await user.type(screen.getByRole("searchbox", { name: "Filter title" }), "alpha");
    expect(titles()).toEqual(["Alpha talk"]);
    expect(screen.getByText("1 of 3 rows match")).toBeInTheDocument();
  });

  it("opens a nested value in the row sheet and switches to the JSON view", async () => {
    const user = userEvent.setup();
    server.use(http.post(`${API}/raw/elasticsearch`, () => esOk()));
    renderStage();
    await editor();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await screen.findByText("Done · 12 ms");

    await user.click(screen.getAllByRole("button", { name: "Open tags, Array[2]" })[0] as HTMLElement);
    const sheet = await screen.findByRole("dialog", { name: "Row" });
    expect(within(sheet).getByText('"x"')).toBeInTheDocument();
    await user.click(within(sheet).getByRole("button", { name: "Close row" }));

    await user.click(screen.getByRole("button", { name: "JSON" }));
    expect(screen.getByRole("list", { name: "Result JSON" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Graph" })).not.toBeInTheDocument();
  });

  it("reads one row in full in the sheet, walks the rows and shows it as JSON", async () => {
    const user = userEvent.setup();
    const long = "Redis keeps its data in memory and writes snapshots to disk. ".repeat(8);
    server.use(
      http.post(`${API}/raw/elasticsearch`, () =>
        esOk({
          hits: [
            {
              summary: "a",
              raw: { _index: "meta", _id: "id-one", _score: 2, _source: { title: "One", text: long } },
            },
            {
              summary: "b",
              raw: { _index: "meta", _id: "id-two", _score: 1, _source: { title: "Two", text: "x" } },
            },
          ],
          total: 2,
        }),
      ),
    );
    renderStage();
    await editor();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await screen.findByText("Done · 12 ms");

    // The table clamps the long text; the sheet has all of it, and the columns the table hides.
    expect(screen.getByRole("table")).not.toHaveTextContent(long.trim());
    await user.click(screen.getByRole("button", { name: "Open row 1" }));
    const sheet = await screen.findByRole("dialog", { name: "Row" });
    expect(within(sheet).getByText("Row 1 of 2")).toBeInTheDocument();
    expect(within(sheet).getByText(long.trim())).toBeInTheDocument();
    expect(within(sheet).getByText("_index")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: /Previous/ })).toBeDisabled();

    await user.click(within(sheet).getByRole("button", { name: /Next/ }));
    expect(within(sheet).getByText("Row 2 of 2")).toBeInTheDocument();
    expect(within(sheet).getByText("id-two")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: /Next/ })).toBeDisabled();

    await user.click(within(sheet).getByRole("button", { name: "JSON" }));
    expect(within(sheet).getByRole("list", { name: "Row JSON" })).toBeInTheDocument();
  });

  it("starts empty and constant columns hidden, and brings them back from the column picker", async () => {
    const user = userEvent.setup();
    server.use(http.post(`${API}/raw/elasticsearch`, () => esOk()));
    renderStage();
    await editor();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await screen.findByText("Done · 12 ms");

    const headers = () =>
      within(screen.getByRole("table"))
        .getAllByRole("columnheader")
        .map((h) => h.textContent);
    expect(headers()).not.toContain("_index");
    await user.click(screen.getByRole("button", { name: "Columns" }));
    await user.click(screen.getByRole("checkbox", { name: "_index" }));
    expect(headers()).toContain("_index");
    await user.click(screen.getByRole("checkbox", { name: "title" }));
    expect(headers()).not.toContain("title");
  });

  it("shows a rejected query as an alert with the server's notes", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API}/raw/elasticsearch`, () =>
        HttpResponse.json({
          backend: "elasticsearch",
          app: "ycs",
          ok: false,
          error: "Body must be a JSON object.",
          notes: ["size defaulted to 20"],
          took_ms: 0,
          hits: [],
        }),
      ),
    );
    renderStage();
    await editor();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Query failed");
    expect(alert).toHaveTextContent("Body must be a JSON object.");
    expect(alert).toHaveTextContent("size defaulted to 20");
    expect(screen.getByText("Error")).toBeInTheDocument();
  });

  it("refuses an empty editor without calling the server", async () => {
    const user = userEvent.setup();
    renderStage();
    const box = await editor();
    await user.clear(box);
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The editor is empty.");
  });

  it("keeps a draft per backend and resets one on request", async () => {
    const user = userEvent.setup();
    renderStage();
    let box = (await editor()) as HTMLTextAreaElement;
    await user.clear(box);
    await user.type(box, "my draft");

    await user.click(screen.getByRole("button", { name: "Neo4j" }));
    box = (await editor()) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toContain("MATCH (a)-[r]->(b)"));

    await user.click(screen.getByRole("button", { name: "Elasticsearch" }));
    box = (await editor()) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("my draft"));

    await user.click(screen.getByRole("button", { name: /Reset/ }));
    await waitFor(() => expect(box.value).toContain("multi_match"));
  });

  it("draws a Neo4j result as a graph and shows the selected node's properties", async () => {
    const user = userEvent.setup();
    const node = (id: string, label: string, properties: Record<string, unknown>) => ({
      _kind: "node",
      id,
      labels: [label],
      properties,
    });
    server.use(
      http.post(`${API}/raw/neo4j`, () =>
        HttpResponse.json({
          backend: "neo4j",
          app: "ycs",
          ok: true,
          notes: [],
          took_ms: 5,
          total: 1,
          hits: [
            {
              summary: "row",
              raw: {
                v: node("n1", "Video", { title: "Redis in practice", upload_date: "2026-01-02" }),
                r: {
                  _kind: "relationship",
                  id: "r1",
                  type: "BELONGS_TO",
                  start: "n1",
                  end: "n2",
                  properties: {},
                },
                c: node("n2", "Channel", { name: "Backend Weekly" }),
              },
            },
          ],
        }),
      ),
    );
    renderStage();
    await user.click(await screen.findByRole("button", { name: "Neo4j" }));
    await editor();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await screen.findByText("Done · 5 ms");

    // Nodes came back, so the result opens on the Graph view without a click; the table is one click away.
    expect(screen.getByRole("button", { name: "Graph" })).toHaveAttribute("aria-pressed", "true");
    const legend = await screen.findByRole("list", { name: "Node labels" });
    expect(within(legend).getByText("Video")).toBeInTheDocument();
    expect(within(legend).getByText("Channel")).toBeInTheDocument();

    const card = await screen.findByRole("button", { name: /Redis in practice/ });
    await user.click(card);
    const panel = screen.getByRole("region", { name: "Selected node" });
    expect(within(panel).getByText("upload_date")).toBeInTheDocument();
    expect(within(panel).getByText("2026-01-02")).toBeInTheDocument();
    expect(within(panel).getByText(/BELONGS_TO/)).toBeInTheDocument();
    expect(within(panel).getByText(/Backend Weekly/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Clear" }));
    expect(screen.getByText(/Select a node to see its properties/)).toBeInTheDocument();
  });

  it("opens a Neo4j result of plain values on the table, with no Graph view to pick", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API}/raw/neo4j`, () =>
        HttpResponse.json({
          backend: "neo4j",
          app: "ycs",
          ok: true,
          notes: [],
          took_ms: 3,
          total: 1,
          hits: [{ summary: "row", raw: { title: "Redis in practice" } }],
        }),
      ),
    );
    renderStage();
    await user.click(await screen.findByRole("button", { name: "Neo4j" }));
    await editor();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await screen.findByText("Done · 3 ms");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Table" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Graph" })).not.toBeInTheDocument();
  });

  it("saves a repeated run of the same query once", async () => {
    const user = userEvent.setup();
    const saved: unknown[] = [];
    server.use(
      http.post(`${API}/raw/elasticsearch`, () => esOk()),
      http.post(`${API}/history`, async ({ request }) => {
        saved.push(await request.json());
        return HttpResponse.json({ id: saved.length });
      }),
    );
    renderStage();
    await editor();
    // "Run" is the button's name only while idle; it reads "Running…" during a run.
    const run = async () => await user.click(await screen.findByRole("button", { name: "Run" }));
    await run();
    await waitFor(() => expect(saved).toHaveLength(1));
    await run();
    await screen.findByRole("button", { name: "Run" });
    await new Promise((resolve) => setTimeout(resolve, 50)); // a wrongly sent save would land by now
    expect(saved).toHaveLength(1);

    // A different assistant prompt is a different entry; the same query and prompt again are not.
    await user.type(screen.getByLabelText("Describe the query"), "same again");
    await run();
    await waitFor(() => expect(saved).toHaveLength(2));
    await run();
    await screen.findByRole("button", { name: "Run" });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saved).toHaveLength(2);
  });

  it("fills the editor from the assistant and reports the outcome", async () => {
    const user = userEvent.setup();
    let seen: unknown = null;
    server.use(
      http.post(`${API}/ai/elasticsearch`, async ({ request }) => {
        seen = await request.json();
        return sse([
          { event: "start", phase: "generate" },
          { event: "model", model: "vendor-a/model-x" },
          { event: "chunk", data: '```json\n{"size": 1}\n```' },
          { event: "done", ok: true, error: null, final: '{"size": 1}' },
        ]);
      }),
    );
    renderStage();
    const box = (await editor()) as HTMLTextAreaElement;
    const generate = screen.getByRole("button", { name: "Generate" });
    expect(generate).toBeDisabled();
    await user.type(screen.getByLabelText("Describe the query"), "one video");
    await user.click(generate);

    await waitFor(() => expect(box.value).toBe('{"size": 1}'));
    expect(await screen.findByText("Generated")).toBeInTheDocument();
    expect(seen).toMatchObject({
      app: "ycs",
      prompt: "one video",
      previous: expect.stringContaining("multi_match"),
    });
    expect(screen.queryByText("vendor-a/model-x")).not.toBeInTheDocument();
  });

  it("shows a failed generation", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${API}/ai/elasticsearch`, () =>
        sse([
          { event: "start" },
          { event: "done", ok: false, error: "only read queries are allowed", final: "" },
        ]),
      ),
    );
    renderStage();
    await editor();
    await user.type(screen.getByLabelText("Describe the query"), "delete everything");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByText("Rejected: only read queries are allowed")).toBeInTheDocument();
  });

  it("lists saved queries, restores one and deletes another", async () => {
    const user = userEvent.setup();
    let items = [
      {
        id: 2,
        backend: "elasticsearch",
        body: '{"size": 3}',
        prompt: "three of them",
        created_at: "2026-10-29T10:00:00Z",
      },
      {
        id: 1,
        backend: "elasticsearch",
        body: '{"size": 1}',
        prompt: "",
        created_at: "2026-10-28T10:00:00Z",
      },
    ];
    const deleted: string[] = [];
    server.use(
      http.get(`${API}/history`, () => HttpResponse.json({ items, total: items.length })),
      http.delete(`${API}/history/:id`, ({ params }) => {
        deleted.push(String(params.id));
        items = items.filter((item) => String(item.id) !== params.id);
        return HttpResponse.json({ deleted: 1 });
      }),
    );
    renderStage();
    const box = (await editor()) as HTMLTextAreaElement;
    await user.click(screen.getByRole("button", { name: /History/ }));
    const sheet = await screen.findByRole("dialog", { name: "Query history" });
    expect(await within(sheet).findByText("three of them")).toBeInTheDocument();

    await user.click(within(sheet).getAllByRole("button", { name: "Delete" })[1] as HTMLElement);
    await waitFor(() => expect(deleted).toEqual(["1"]));
    await waitFor(() => expect(within(sheet).getAllByRole("listitem")).toHaveLength(1));

    await user.click(within(sheet).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(box.value).toBe('{"size": 3}'));
    expect(screen.queryByRole("dialog", { name: "Query history" })).not.toBeInTheDocument();
  });

  it("shows a query run several times once, and deleting it removes every run", async () => {
    const user = userEvent.setup();
    const same = {
      backend: "elasticsearch",
      body: '{"size": 3}',
      prompt: "",
    };
    let items = [
      { id: 3, created_at: "2026-10-29T12:00:00Z", ...same },
      { id: 2, created_at: "2026-10-29T11:00:00Z", ...same },
      { id: 1, created_at: "2026-10-29T10:00:00Z", ...same },
    ];
    const deleted: string[] = [];
    server.use(
      http.get(`${API}/history`, () => HttpResponse.json({ items, total: items.length })),
      http.delete(`${API}/history/:id`, ({ params }) => {
        deleted.push(String(params.id));
        items = items.filter((item) => String(item.id) !== params.id);
        return HttpResponse.json({ deleted: 1 });
      }),
    );
    renderStage();
    await editor();
    await user.click(screen.getByRole("button", { name: /History/ }));
    const sheet = await screen.findByRole("dialog", { name: "Query history" });
    expect(await within(sheet).findByText(/run 3 times/)).toBeInTheDocument();
    expect(within(sheet).getAllByRole("listitem")).toHaveLength(1);

    await user.click(within(sheet).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleted.sort()).toEqual(["1", "2", "3"]));
    expect(await within(sheet).findByText(/Nothing saved yet/)).toBeInTheDocument();
  });

  it("lists a store with many groups folded, filters them and inserts a query into the editor", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API}/schema/neo4j`, () =>
        HttpResponse.json({
          backend: "neo4j",
          app: "ycs",
          cached_at: 1,
          schema: {
            labels: ["Video", "Channel", "AI Model", "Author", "Award"],
            node_properties: {
              Video: [{ name: "title", types: ["String"] }],
              Channel: [{ name: "name", types: ["String"] }],
              "AI Model": [{ name: "id", types: ["String"] }],
              Author: [],
              Award: [],
            },
          },
        }),
      ),
    );
    renderStage();
    await user.click(await screen.findByRole("button", { name: "Neo4j" }));
    const box = (await editor()) as HTMLTextAreaElement;
    await user.click(screen.getByRole("button", { name: /Schema/ }));
    const sheet = await screen.findByRole("dialog", { name: "Schema" });

    // Five groups: folded, so no property rows yet.
    expect(await within(sheet).findByText("(:Video)")).toBeInTheDocument();
    expect(within(sheet).getByText("5 groups")).toBeInTheDocument();
    expect(within(sheet).queryByText("title")).not.toBeInTheDocument();

    await user.type(within(sheet).getByRole("searchbox", { name: "Filter the schema" }), "model");
    expect(within(sheet).getByText("1 of 5 match")).toBeInTheDocument();
    expect(within(sheet).queryByText("(:Video)")).not.toBeInTheDocument();

    await user.click(within(sheet).getByRole("button", { name: "Insert query for (:AI Model)" }));
    await waitFor(() => expect(box.value).toBe("MATCH (n:`AI Model`)\nRETURN n\nLIMIT 25"));
    expect(screen.queryByRole("dialog", { name: "Schema" })).not.toBeInTheDocument();
  });

  it("shows the backend's schema in the sheet", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API}/schema/elasticsearch`, () =>
        HttpResponse.json({
          backend: "elasticsearch",
          app: "ycs",
          cached_at: 1,
          schema: {
            indices: {
              youtube_metadata: { doc_count: 40, mappings: { properties: { title: { type: "text" } } } },
            },
          },
        }),
      ),
    );
    renderStage();
    await editor();
    await user.click(screen.getByRole("button", { name: /Schema/ }));
    const sheet = await screen.findByRole("dialog", { name: "Schema" });
    expect(await within(sheet).findByText("youtube_metadata")).toBeInTheDocument();
    expect(within(sheet).getByText("40 documents")).toBeInTheDocument();
    expect(within(sheet).getByText("title")).toBeInTheDocument();
  });
});
