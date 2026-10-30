import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const IDS = Array.from({ length: 6 }, (_, i) => `vid${i}aaaaaaa`);
const LONG_TITLE = `${"A very long video title that keeps going and going ".repeat(4)}end`;
const ITEMS = IDS.map((id, i) => ({
  id,
  title: i === 0 ? LONG_TITLE : `Talk number ${i}`,
  channel: "React Channel",
}));
const LIBRARY = IDS.map((id, i) => ({
  video_id: id,
  title: i === 0 ? LONG_TITLE : `Talk number ${i}`,
  channel: "React Channel",
  status: i === 1 ? "partial" : i === 2 ? "error" : "done",
  view_count: 1200 * (i + 1),
  like_count: 40,
  duration_string: `${i + 3}:07`,
  upload_date: "20240105",
  thumbnail: `https://i.ytimg.com/vi/${id}/mqdefault.jpg`,
  transcript_length: 9000,
  transcript_langs: ["en"],
  entity_count: 12,
}));

async function mockIngestion(page: Page) {
  await page.addInitScript(
    (payload) => localStorage.setItem("ycs:pipeline:active", JSON.stringify(payload)),
    { extract: "e1", qdrant: "e1", neo4j: "e1", video_ids: IDS, startedAt: Date.now() - 95_000 },
  );
  await page.route("https://i.ytimg.com/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#444"/></svg>',
    }),
  );
  await page.route("**/api/v1/ycs/admin/task/e1", (route) =>
    route.fulfill({
      json: {
        task_id: "e1",
        state: "PROGRESS",
        meta: {
          phase: "transcription",
          current: 3,
          total: 6,
          all_items: ITEMS,
          current_item: { id: IDS[3], title: "Talk number 3" },
          completed_ids: IDS.slice(0, 3),
          failed_ids: [IDS[4]],
          no_transcript_ids: [],
        },
        result: null,
      },
    }),
  );
  await page.route("**/api/v1/ycs/admin/pipeline/e1/stream/qdrant", (route) =>
    route.fulfill({
      json: {
        task_id: "e1",
        state: "PROGRESS",
        meta: { current: 2, total: 6, completed_ids: IDS.slice(0, 2) },
        result: null,
      },
    }),
  );
  await page.route("**/api/v1/ycs/admin/pipeline/e1/stream/neo4j", (route) =>
    route.fulfill({ json: { task_id: "e1", state: "PENDING", meta: {}, result: null } }),
  );
  await page.route("**/api/v1/ycs/admin/pipeline/e1/llm-counters", (route) =>
    route.fulfill({
      json: {
        total: { calls: 31, tokens_in: 412300, tokens_out: 18400, reasoning_tokens: 0 },
        by_node: {
          [IDS[0] ?? ""]: {
            by_model: {
              "vendor-a/model-x": { calls: 20, tokens_in: 300000, tokens_out: 12000, reasoning_tokens: 0 },
              "vendor-b/model-y": { calls: 11, tokens_in: 112300, tokens_out: 6400, reasoning_tokens: 0 },
            },
          },
        },
      },
    }),
  );
  await page.route("**/api/v1/ycs/admin/videos/facets", (route) =>
    route.fulfill({
      json: {
        statuses: [
          { key: "done", label: "Done", count: 4 },
          { key: "partial", label: "Partial", count: 1 },
        ],
        channels: [{ channel_id: "UC1", channel: "React Channel", video_count: 6 }],
        languages: [{ key: "en", label: "en", count: 6 }],
      },
    }),
  );
  await page.route("**/api/v1/ycs/admin/videos?**", (route) =>
    route.fulfill({
      json: { items: LIBRARY, total: LIBRARY.length, returned: LIBRARY.length, offset: 0, limit: 50 },
    }),
  );
}

async function open(page: Page) {
  await mockIngestion(page);
  await page.goto("/ycs/ingestion");
  await expect(page.getByRole("list", { name: "Pipeline phases" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Ingested videos" }).getByRole("listitem")).toHaveCount(6);
}

test.describe("YouTube Content Search · Ingestion", () => {
  test("shows the run as four steps with a word each, and one status line for a screen reader", async ({
    page,
  }) => {
    await open(page);
    const steps = page.getByRole("list", { name: "Pipeline phases" });
    await expect(steps.getByRole("progressbar")).toHaveCount(4);
    await expect(steps.getByRole("progressbar", { name: "Phase 1 · Playwright" })).toHaveAttribute(
      "aria-valuenow",
      /\d+/,
    );
    await expect(steps.getByText("Queued").first()).toBeVisible(); // Neo4j waits behind the others
    await expect(page.getByRole("region", { name: "Pipeline progress" })).not.toHaveAttribute(
      "aria-live",
      /.*/,
    );
    await expect(page.getByRole("status").filter({ hasText: "Pipeline running." })).toBeAttached();
    // Stop is for a run that works; Retry waits until it has stopped.
    await expect(page.getByRole("button", { name: "Stop" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Retry" })).toBeDisabled();
  });

  test("lists the videos of the run in a sheet, failed first, and the LLM usage by model in another", async ({
    page,
  }) => {
    await open(page);
    await page.getByRole("button", { name: "Videos (6)" }).click();
    const videos = page.getByRole("dialog", { name: "Videos in this pipeline" });
    const rows = videos.getByRole("region", { name: "Per-video pipeline progress" }).getByRole("row");
    await expect(rows.nth(1)).toContainText("Talk number 4"); // the failed one, after the header row
    await expect(videos.getByText("Failed only (1)")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(videos).toBeHidden();

    await page.getByRole("button", { name: "LLM usage" }).click();
    const usage = page.getByRole("dialog", { name: "Pipeline LLM usage" });
    await expect(usage.getByRole("row", { name: /vendor-a\/model-x/ })).toContainText("20");
    await expect(usage.getByRole("row", { name: /vendor-b\/model-y/ })).toContainText("11");
  });

  test("keeps Wipe cache apart from Stop and Retry, behind a confirm that says what it deletes", async ({
    page,
  }) => {
    await open(page);
    const wipe = page.getByRole("button", { name: "Wipe cache" });
    const stop = page.getByRole("button", { name: "Stop" });
    const wipeBox = await wipe.boundingBox();
    const stopBox = await stop.boundingBox();
    if (!wipeBox || !stopBox) throw new Error("no box");
    expect(wipeBox.y).toBeGreaterThan(stopBox.y + stopBox.height); // a row below the run's own actions

    await wipe.click();
    const dialog = page.getByRole("dialog", { name: "Wipe cache for these videos?" });
    await expect(dialog).toContainText("6 videos");
    await expect(dialog).toContainText("cannot be undone");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
  });

  test("the Library selects a Shift range and shows a bar that sticks to the bottom of the screen", async ({
    page,
  }) => {
    await open(page);
    await page.getByRole("checkbox", { name: "Select Talk number 1" }).click();
    await page.getByRole("checkbox", { name: "Select Talk number 3" }).click({ modifiers: ["Shift"] });
    const bar = page.getByRole("region", { name: "Selected videos" });
    await expect(bar).toContainText("3 selected");
    await expect(bar.getByRole("button", { name: "Re-ingest" })).toBeVisible();
    await expect(bar.getByRole("button", { name: "Delete" })).toBeVisible();
    const box = await bar.boundingBox();
    const viewport = page.viewportSize();
    if (!box || !viewport) throw new Error("no box");
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  });

  test("fits the screen and keeps text at 12px or more, with a selection made", async ({ page }) => {
    await open(page);
    await page.getByRole("checkbox", { name: "Select Talk number 2" }).click();
    const { overflow, small } = await page.evaluate(() => {
      const smallText = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
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
    test(`has no WCAG 2.2 AA violations, on the page and with the video sheet open (${scheme})`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page);
      await page.getByRole("checkbox", { name: "Select Talk number 2" }).click();
      const check = async (label: string) => {
        const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
        expect(
          results.violations,
          `${label}: ${JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`,
        ).toEqual([]);
      };
      await check("page");
      await page.getByRole("button", { name: "Videos (6)" }).click();
      await expect(page.getByRole("dialog", { name: "Videos in this pipeline" })).toBeVisible();
      await check("video sheet");
    });
  }
});
