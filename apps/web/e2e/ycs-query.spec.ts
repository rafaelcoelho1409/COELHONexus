import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const LONG_TITLE = `${"A very long video title that keeps going and going ".repeat(3)}end`;
const LONG_TEXT = "Redis keeps its data in memory and writes snapshots to disk. ".repeat(60);

const ES_HITS = Array.from({ length: 6 }, (_, i) => ({
  summary: `v${i}`,
  raw: {
    _index: "youtube_metadata",
    _id: `vid${i}aaaaaaa`,
    _score: 4.5 - i / 3,
    _source: {
      title: i === 0 ? LONG_TITLE : `Talk number ${i + 1}`,
      channel: "Backend Weekly",
      view_count: 1000 * (i + 1),
      tags: ["redis", "databases"],
      content: LONG_TEXT,
      webpage_url: `https://www.youtube.com/watch?v=vid${i}aaaaaaa&list=${"x".repeat(80)}`,
    },
  },
}));

const node = (id: string, label: string, properties: Record<string, unknown>) => ({
  _kind: "node",
  id,
  labels: [label],
  properties,
});
const rel = (id: string, start: string, end: string, type: string) => ({
  _kind: "relationship",
  id,
  type,
  start,
  end,
  properties: {},
});

const NEO4J_HITS = [
  {
    summary: "row",
    raw: {
      v: node("n1", "Video", { title: LONG_TITLE, upload_date: "2026-01-02" }),
      r: rel("r1", "n1", "n2", "BELONGS_TO"),
      c: node("n2", "Channel", { name: "Backend Weekly" }),
    },
  },
  {
    summary: "row",
    raw: {
      v: node("n3", "Video", { title: "Postgres basics" }),
      r: rel("r2", "n3", "n2", "BELONGS_TO"),
      c: node("n2", "Channel", { name: "Backend Weekly" }),
    },
  },
  {
    summary: "row",
    raw: {
      d: node("n4", "Document", { video_id: "vid0aaaaaaa", text: LONG_TEXT }),
      r: rel("r3", "n4", "n5", "MENTIONS"),
      e: node("n5", "__Entity__", { id: "Redis" }),
    },
  },
];

async function mockQuery(page: Page) {
  await page.route("**/api/v1/ycs/query/namespaces", (route) =>
    route.fulfill({
      json: {
        matrix: {
          ycs: {
            elasticsearch: { available: true, label: "YCS · metadata + transcripts" },
            qdrant: { available: true, label: "YCS · transcript chunks" },
            neo4j: { available: true, label: "YCS · entities + videos" },
          },
        },
      },
    }),
  );
  await page.route("**/api/v1/ycs/query/raw/elasticsearch", (route) =>
    route.fulfill({
      json: {
        backend: "elasticsearch",
        app: "ycs",
        ok: true,
        notes: [],
        took_ms: 12,
        total: 6,
        hits: ES_HITS,
      },
    }),
  );
  await page.route("**/api/v1/ycs/query/raw/neo4j", (route) =>
    route.fulfill({
      json: { backend: "neo4j", app: "ycs", ok: true, notes: [], took_ms: 8, total: 3, hits: NEO4J_HITS },
    }),
  );
  await page.route("**/api/v1/ycs/query/ai/elasticsearch", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: [
        { event: "start", phase: "generate" },
        { event: "chunk", data: '{"size": 2}' },
        { event: "done", ok: true, error: null, final: '{"size": 2, "query": {"match_all": {}}}' },
      ]
        .map((event) => `data: ${JSON.stringify(event)}\n\n`)
        .join(""),
    }),
  );
  await page.route("**/api/v1/ycs/query/schema/elasticsearch", (route) =>
    route.fulfill({
      json: {
        backend: "elasticsearch",
        app: "ycs",
        cached_at: 1,
        schema: {
          indices: {
            youtube_metadata: {
              doc_count: 1204,
              mappings: { properties: { title: { type: "text" }, view_count: { type: "long" } } },
            },
          },
        },
      },
    }),
  );
  await page.route("**/api/v1/ycs/query/schema/neo4j", (route) =>
    route.fulfill({
      json: {
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
          relationship_patterns: [{ src: "Video", rel: "BELONGS_TO", dst: "Channel", count: 4 }],
        },
      },
    }),
  );
  await page.route("**/api/v1/ycs/query/history**", (route) => {
    if (route.request().method() === "POST") return route.fulfill({ json: { id: 3 } });
    return route.fulfill({
      json: {
        items: [
          {
            id: 2,
            backend: "elasticsearch",
            app: "ycs",
            body: '{"size": 3}',
            prompt: "three of them",
            created_at: "2026-10-29T10:00:00Z",
          },
        ],
        total: 1,
      },
    });
  });
}

async function runEs(page: Page) {
  await mockQuery(page);
  await page.goto("/ycs/query");
  const editor = page.getByRole("textbox", { name: "Query editor" });
  await expect(editor).toBeVisible();
  await page.getByRole("button", { name: /^Run/ }).click();
  await expect(page.getByText("Done · 12 ms")).toBeVisible();
}

async function runNeo4j(page: Page) {
  await page.getByRole("button", { name: "Neo4j" }).click();
  await expect(page.getByRole("textbox", { name: "Query editor" })).toBeVisible();
  await page.getByRole("button", { name: /^Run/ }).click();
  await expect(page.getByText("Done · 8 ms")).toBeVisible();
}

test.describe("YouTube Content Search · Query", () => {
  test("runs from the keyboard in the real editor, and Escape then Tab leaves it", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "a phone has no hardware keyboard to test");
    await mockQuery(page);
    await page.goto("/ycs/query");
    const editor = page.getByRole("textbox", { name: "Query editor" });
    await editor.click();
    await page.keyboard.press("Control+Enter");
    await expect(page.getByText("Done · 12 ms")).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByRole("row")).toHaveCount(7); // header + six rows

    await editor.click();
    await page.keyboard.press("Escape");
    await page.keyboard.press("Tab");
    const stillInside = await page.evaluate(() => document.activeElement?.closest(".cm-editor") !== null);
    expect(stillInside, "focus is trapped in the editor").toBe(false);
  });

  test("the assistant writes into the editor, and the result sorts and filters", async ({ page }) => {
    await mockQuery(page);
    await page.goto("/ycs/query");
    await page.getByLabel("Describe the query").fill("two videos");
    await page.getByRole("button", { name: "Generate" }).click();
    await expect(page.getByText("Generated")).toBeVisible();
    await expect(page.locator(".cm-content")).toContainText('"match_all"');

    await page.getByRole("button", { name: /^Run/ }).click();
    await expect(page.getByText("Done · 12 ms")).toBeVisible();
    await page.getByRole("button", { name: /^view_count/ }).click();
    await page.getByRole("button", { name: /^view_count/ }).click();
    await expect(page.getByRole("columnheader", { name: /view_count/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    await page.getByRole("button", { name: "Filter columns" }).click();
    await page.getByRole("searchbox", { name: "Filter title" }).fill("number 3");
    await expect(page.getByText("1 of 6 rows match")).toBeVisible();
  });

  test("draws a Neo4j result as a graph with edges, and selecting a node shows its properties", async ({
    page,
  }) => {
    await mockQuery(page);
    await page.goto("/ycs/query");
    await runNeo4j(page);
    await page.getByRole("button", { name: "Graph" }).click();
    await expect(page.getByRole("list", { name: "Node labels" }).getByText("Document")).toBeVisible();
    const canvas = page.locator(".query-graph");
    await expect(canvas.locator(".react-flow__node")).toHaveCount(5);
    await expect(canvas.locator(".react-flow__edge")).toHaveCount(3);
    await canvas.getByRole("button", { name: /Postgres basics/ }).click();
    const panel = page.getByRole("region", { name: "Selected node" });
    await expect(panel.getByText("BELONGS_TO")).toBeVisible();
  });

  test("long transcripts stay a few lines tall, and a row opens in full from the keyboard", async ({
    page,
  }) => {
    await runEs(page);
    const first = page.locator("tbody tr").first();
    const box = await first.boundingBox();
    expect(box?.height ?? 0, "a result row grew with its text").toBeLessThan(170);

    await page.getByRole("button", { name: "Open row 1" }).focus();
    await page.keyboard.press("Enter");
    const sheet = page.getByRole("dialog", { name: "Row" });
    await expect(sheet.getByText("Row 1 of 6")).toBeVisible();
    // The sheet has the whole value the table clamped, and the columns the table hid.
    await expect(sheet.getByText("_index", { exact: true })).toBeVisible();
    await expect(sheet.getByText(LONG_TEXT.trim())).toBeVisible();
    await sheet.getByRole("button", { name: /Next/ }).click();
    await expect(sheet.getByText("Row 2 of 6")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    await expect(page.getByRole("button", { name: "Open row 1" })).toBeFocused(); // back where it was opened from
  });

  test("on a desktop the results start above the fold, under a short editor", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "the fold is a desktop measure");
    await page.setViewportSize({ width: 1440, height: 900 });
    await runEs(page);
    const heading = await page.getByRole("heading", { name: "Results" }).boundingBox();
    expect(heading?.y ?? 9999, "the results heading sits below the fold").toBeLessThan(720);
    const header = await page.getByRole("columnheader", { name: "title" }).boundingBox();
    expect(header?.y ?? 9999, "the table header sits below the fold").toBeLessThan(880);
  });

  test("Run stays in the header's first row on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await mockQuery(page);
    await page.goto("/ycs/query");
    const run = await page.getByRole("button", { name: "Run" }).boundingBox();
    const title = await page.getByRole("heading", { name: "Elasticsearch" }).boundingBox();
    expect(run, "Run is not on screen").not.toBeNull();
    expect(Math.abs((run?.y ?? 0) - (title?.y ?? 9999)), "Run dropped below the title").toBeLessThan(40);
  });

  test("the schema sheet filters a long list and writes a query into the editor", async ({ page }) => {
    await mockQuery(page);
    await page.goto("/ycs/query");
    await page.getByRole("button", { name: "Neo4j" }).click();
    await page.getByRole("button", { name: /Schema/ }).click();
    const sheet = page.getByRole("dialog", { name: "Schema" });
    await expect(sheet.getByText("(:Video)", { exact: true })).toBeVisible();
    await expect(sheet.getByText("title", { exact: true })).toHaveCount(0); // folded
    await sheet.getByRole("searchbox", { name: "Filter the schema" }).fill("model");
    await expect(sheet.getByText("(:Video)", { exact: true })).toHaveCount(0);
    await sheet.getByRole("button", { name: "Insert query for (:AI Model)" }).click();
    await expect(sheet).toBeHidden();
    await expect(page.locator(".cm-content")).toContainText("MATCH (n:`AI Model`)");
  });

  test("on a phone nothing scrolls sideways and the editor stays usable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await runEs(page);
    await page.getByRole("button", { name: "Filter columns" }).click();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: /Schema/ }).click();
    await expect(page.getByRole("dialog", { name: "Schema" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Schema" })).toBeHidden();
  });

  test("keeps text at 12px or more, table and graph views alike", async ({ page }) => {
    await runEs(page);
    await page.getByRole("button", { name: "Filter columns" }).click();
    const measure = () =>
      page.evaluate(() => {
        const small = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
          const hasText = Array.from(element.childNodes).some(
            (child) => child.nodeType === Node.TEXT_NODE && (child.textContent ?? "").trim() !== "",
          );
          return hasText && Number.parseFloat(getComputedStyle(element).fontSize) < 12;
        });
        return {
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          small: small.map((element) => `${element.tagName}.${element.className}`),
        };
      });
    let seen = await measure();
    expect(seen.overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
    expect(seen.small, "text under 12px (table)").toEqual([]);

    await runNeo4j(page);
    await page.getByRole("button", { name: "Graph" }).click();
    await expect(page.locator(".query-graph .react-flow__node")).toHaveCount(5);
    seen = await measure();
    expect(seen.small, "text under 12px (graph)").toEqual([]);
  });

  const axeCheck = async (page: Page, label: string) => {
    const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
    expect(
      results.violations,
      `${label}: ${JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`,
    ).toEqual([]);
  };

  for (const scheme of ["dark", "light"] as const) {
    test(`has no WCAG 2.2 AA violations in the table, its pickers, the row sheet and the JSON view (${scheme})`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await runEs(page);
      const check = (label: string) => axeCheck(page, label);
      await check("table");
      await page.getByRole("button", { name: "Filter columns" }).click();
      await check("table with filters");
      await page.getByRole("button", { name: "Columns", exact: true }).click();
      await check("table with the column picker");
      await page.getByRole("button", { name: "Open row 1" }).click();
      await expect(page.getByRole("dialog", { name: "Row" }).getByText("Row 1 of 6")).toBeVisible();
      await check("row sheet");
      await page.getByRole("dialog", { name: "Row" }).getByRole("button", { name: "JSON" }).click();
      await check("row sheet as JSON");
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "JSON", exact: true }).click();
      await check("json");
    });

    test(`has no WCAG 2.2 AA violations in the schema and history sheets and the graph (${scheme})`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await runEs(page);
      const check = (label: string) => axeCheck(page, label);
      await page.getByRole("button", { name: /Schema/ }).click();
      await expect(page.getByRole("dialog", { name: "Schema" }).getByText("youtube_metadata")).toBeVisible();
      await check("schema sheet");
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Neo4j" }).click();
      await page.getByRole("button", { name: /Schema/ }).click();
      await expect(page.getByRole("dialog", { name: "Schema" }).getByText("(:Video)")).toBeVisible();
      await check("schema sheet, folded groups");
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Elasticsearch" }).click();
      await page.getByRole("button", { name: /History/ }).click();
      await expect(
        page.getByRole("dialog", { name: "Query history" }).getByText("three of them"),
      ).toBeVisible();
      await check("history sheet");
      await page.keyboard.press("Escape");

      await runNeo4j(page);
      await page.getByRole("button", { name: "Graph" }).click();
      await expect(page.locator(".query-graph .react-flow__node")).toHaveCount(5);
      await check("graph");
    });
  }
});
