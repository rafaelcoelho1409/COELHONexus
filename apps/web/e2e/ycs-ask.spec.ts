import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const LONG_TITLE = `${"A very long video title that keeps going and going ".repeat(3)}end`;
const SOURCES = Array.from({ length: 5 }, (_, i) => ({
  title: i === 0 ? LONG_TITLE : `Talk number ${i + 1}`,
  url: `https://www.youtube.com/watch?v=vid${i}aaaaaaa`,
  video_id: `vid${i}aaaaaaa`,
  channel: "Backend Weekly",
  snippet:
    "Redis keeps its data in memory and writes snapshots to disk, so a restart can load the last snapshot back quickly. ".repeat(
      2,
    ),
  source: ["qdrant_hybrid", "neo4j_graph", "elasticsearch"][i % 3] ?? "",
}));

const ANSWER = [
  "## What the talks say",
  "",
  `Redis is an in-memory store [Video: ${LONG_TITLE}] and Postgres keeps rows on disk [Video: Talk number 2][Video: Talk number 3].`,
  "",
  "```",
  "SET greeting hello  # a line of code that is long enough to need sideways room in a narrow column of the page",
  "```",
  "",
  "| Store | Where data lives | Typical latency under load |",
  "| --- | --- | --- |",
  "| Redis | memory | well under a millisecond for simple reads |",
  "| Postgres | disk and cache | a few milliseconds for indexed lookups |",
  "",
  `More at https://example.test/${"a-very-long-path-segment/".repeat(6)}end [Video: Talk number 4]`,
].join("\n");

const EVENTS = [
  { node: "_meta", turn_id: 7 },
  { node: "classify_query", mode: "standard" },
  { node: "retrieve", documents: [], document_count: 5 },
  { node: "grade_documents" },
  { node: "generate", generation: ANSWER, citations: SOURCES },
  { node: "end", duration_ms: 14_000, usage: { total: { calls: 3, tokens_in: 4200, tokens_out: 900 } } },
];

const HISTORY = [
  {
    id: 5,
    question: "What is Redis?",
    answer: ANSWER,
    mode: "standard",
    created_at: "2026-10-29T10:00:00Z",
    thinking_state: { channel_ids: ["UC1"], duration_ms: 14_000, citations: SOURCES },
  },
];

async function mockAsk(page: Page, history: unknown[] = []) {
  await page.route("https://i.ytimg.com/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#444"/></svg>',
    }),
  );
  await page.route("**/api/v1/ycs/admin/ingested-channels", (route) =>
    route.fulfill({
      json: {
        items: [
          { channel_id: "UC1", channel: "Backend Weekly", video_count: 12 },
          { channel_id: "UC2", channel: "Data Hour", video_count: 4 },
        ],
      },
    }),
  );
  await page.route("**/api/v1/ycs/agents/history/*", (route) => route.fulfill({ json: { items: history } }));
  await page.route("**/api/v1/ycs/agents/threads", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            thread_id: "t1",
            first_question: "What is Redis?",
            turn_count: 1,
            last_seen: "2026-10-29T10:00:00Z",
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/ycs/agents/search/stream", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: `${EVENTS.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")}\n\n`,
    }),
  );
}

async function askOnce(page: Page) {
  await mockAsk(page);
  await page.goto("/ycs/ask");
  await expect(page.getByRole("heading", { name: "Ask about your videos" })).toBeVisible();
  await page.getByRole("textbox", { name: "Ask about your videos" }).fill("What is Redis?");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Answered in 14.0s")).toBeVisible();
}

test.describe("YouTube Content Search · Ask", () => {
  test("answers with pills that light up their card in the Sources rail", async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== "desktop",
      "the rail is the wide layout; the phone test covers the sheet",
    );
    await askOnce(page);
    await expect(page.getByRole("button", { name: "Thought for 14.0s" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    const rail = page.getByRole("complementary", { name: "Sources" });
    await expect(rail.getByRole("listitem")).toHaveCount(5);
    await page.getByRole("button", { name: "Source 3: Talk number 3" }).click();
    await expect(rail.getByRole("listitem").nth(2)).toHaveAttribute("aria-current", "true");
    await expect(rail.getByRole("listitem").nth(0)).not.toHaveAttribute("aria-current", "true");
    // the trace opens on demand and shows the four steps
    await page.getByRole("button", { name: "Thought for 14.0s" }).click();
    await expect(page.getByRole("list").filter({ hasText: "Retrieve" }).getByRole("listitem")).toHaveCount(4);
  });

  test("Enter sends, Shift+Enter writes a line, / jumps to the box, and the scope locks after the first message", async ({
    page,
  }) => {
    await mockAsk(page);
    await page.goto("/ycs/ask");
    await page.getByRole("button", { name: "What contradictions appear between sources?" }).click();
    const box = page.getByRole("textbox", { name: "Ask about your videos" });
    await expect(box).toBeFocused();
    await box.press("Shift+Enter");
    await box.pressSequentially("and more");
    await expect(box).toHaveValue("What contradictions appear between sources?\nand more");
    await box.blur();
    await page.keyboard.press("/");
    await expect(box).toBeFocused();
    await box.press("Enter");
    await expect(page.getByText("Answered in 14.0s")).toBeVisible();
    await expect(page.getByText(/locked to this thread/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Channels:/ })).toHaveCount(0);
  });

  test("restores a saved thread from the address, with its scope locked and actions on the turn", async ({
    page,
  }) => {
    await mockAsk(page, HISTORY);
    await page.goto("/ycs/ask?thread=t1");
    await expect(page.getByRole("heading", { name: "You asked: What is Redis?" })).toBeVisible();
    await expect(page.getByText(/locked to this thread/)).toBeVisible();
    for (const name of [/^Sources \(5\)/, "Copy", "Regenerate", "Branch"]) {
      // `exact`: the code block in the answer has a "Copy code" button of its own
      await expect(page.getByRole("button", { name, exact: true })).toBeEnabled();
    }
    await page.getByRole("button", { name: /Thread:/ }).click();
    await expect(page.getByRole("button", { name: /^What is Redis\?/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: /Thread:/ })).toHaveAttribute("aria-expanded", "false");
  });

  test("the empty first screen has the message box at the bottom of the screen and no sources note", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "the desktop viewport is the measure");
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockAsk(page);
    await page.goto("/ycs/ask");
    await expect(page.getByRole("heading", { name: "Ask about your videos" })).toBeVisible();
    await expect(page.getByText(/no sources/i)).toHaveCount(0);
    const box = await page.getByRole("textbox", { name: "Ask about your videos" }).boundingBox();
    expect((box?.y ?? 0) + (box?.height ?? 0), "the message box floats mid-page").toBeGreaterThan(760);
  });

  test("a failed answer is an error band with Retry, and the status never says Done", async ({ page }) => {
    await mockAsk(page);
    let asked = 0;
    await page.route("**/api/v1/ycs/agents/search/stream", (route) => {
      asked += 1;
      const events =
        asked === 1
          ? [
              { node: "_meta", turn_id: 7 },
              { node: "retrieve", documents: [], document_count: 5 },
              {
                node: "generate",
                generation: "Error generating answer: Connection error.",
                citations: SOURCES,
              },
              { node: "end", duration_ms: 20_000 },
            ]
          : EVENTS;
      return route.fulfill({
        contentType: "text/event-stream",
        body: `${events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")}\n\n`,
      });
    });
    await page.goto("/ycs/ask");
    await page.getByRole("textbox", { name: "Ask about your videos" }).fill("What is Redis?");
    await page.keyboard.press("Enter");
    const band = page.getByRole("alert").filter({ hasText: "The answer could not be generated." });
    await expect(band).toBeVisible();
    await expect(band).toContainText("Connection error.");
    await expect(page.getByText("Failed: Connection error.")).toBeVisible();
    await expect(page.getByText("Done.")).toHaveCount(0);
    await expect(page.getByText(/Error generating answer/)).toHaveCount(0);
    const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
    expect(
      results.violations,
      JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)])),
    ).toEqual([]);

    await band.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Answered in 14.0s")).toBeVisible();
    await expect(band).toHaveCount(0);
  });

  test("on a phone the toolbar is two rows and the thread panel stays on screen", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await mockAsk(page, HISTORY);
    await page.goto("/ycs/ask?thread=t1");
    await expect(page.getByRole("heading", { name: "You asked: What is Redis?" })).toBeVisible();
    // The scope note and the thread picker share a row; the two actions are icons beside the mode.
    const scope = await page
      .getByText(/locked to this thread/)
      .locator("..")
      .boundingBox();
    const thread = await page.getByRole("button", { name: /Thread:/ }).boundingBox();
    expect(
      Math.abs((scope?.y ?? 0) - (thread?.y ?? 9999)),
      "scope and thread are on separate rows",
    ).toBeLessThan(6);
    const mode = await page.getByLabel("Fast").locator("..").boundingBox();
    const usage = await page.getByRole("button", { name: "LLM usage" }).boundingBox();
    expect(Math.abs((mode?.y ?? 0) - (usage?.y ?? 9999)), "the actions dropped below the mode").toBeLessThan(
      6,
    );
    expect(usage?.width ?? 999, "the usage button still carries its words").toBeLessThan(60);

    await page.getByRole("button", { name: /Thread:/ }).click();
    const panel = await page.getByRole("button", { name: /^What is Redis\?/ }).boundingBox();
    expect(
      (panel?.x ?? -1) >= 0 && (panel?.x ?? 0) + (panel?.width ?? 9999) <= 390,
      "the thread panel leaves the screen",
    ).toBe(true);
  });

  test("on a phone the sources open in a sheet and nothing scrolls sideways", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await askOnce(page);
    await expect(page.getByRole("complementary", { name: "Sources" })).toHaveCount(0);
    await page.getByRole("button", { name: "Source 2: Talk number 2" }).click();
    const sheet = page.getByRole("dialog", { name: "Sources" });
    await expect(sheet.getByRole("listitem")).toHaveCount(5);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
  });

  test("fits the screen and keeps text at 12px or more, with long titles, a table and a code block", async ({
    page,
  }) => {
    await askOnce(page);
    const { overflow, small } = await page.evaluate(() => {
      const smallText = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
        if (element.closest(".katex")) return false;
        const hasText = Array.from(element.childNodes).some(
          (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
        );
        return hasText && Number.parseFloat(getComputedStyle(element).fontSize) < 12;
      });
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        small: smallText.map((element) => `${element.tagName}.${element.className}`),
      };
    });
    expect(overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
    expect(small, "text under 12px").toEqual([]);
  });

  for (const scheme of ["dark", "light"] as const) {
    test(`has no WCAG 2.2 AA violations, on the page, with the scope panel and with the usage sheet open (${scheme})`, async ({
      page,
    }) => {
      await page.route("**/api/v1/ycs/agents/usage/*", (route) =>
        route.fulfill({
          json: {
            total: { calls: 3, tokens_in: 4200, tokens_out: 900, reasoning_tokens: 0 },
            by_node: {
              generate: { by_model: { "vendor-a/model-x": { calls: 3, tokens_in: 4200, tokens_out: 900 } } },
            },
          },
        }),
      );
      await page.emulateMedia({ colorScheme: scheme });
      await mockAsk(page, HISTORY);
      await page.goto("/ycs/ask?thread=t1");
      await expect(page.getByRole("heading", { name: "You asked: What is Redis?" })).toBeVisible();
      const check = async (label: string) => {
        const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
        expect(
          results.violations,
          `${label}: ${JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`,
        ).toEqual([]);
      };
      await check("page");
      await page.getByRole("button", { name: "Thought for 14.0s" }).click();
      await page.getByRole("button", { name: /Thread:/ }).click();
      await check("thread panel");
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "LLM usage" }).click();
      await expect(page.getByRole("dialog", { name: "Conversation LLM usage" })).toBeVisible();
      await check("usage sheet");
    });
  }
});
