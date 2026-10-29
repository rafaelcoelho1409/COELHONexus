import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

// Real playlists carry long titles and many rows: the page must still fit the screen.
const LONG_TITLE = `${"A very long video title that keeps going and going ".repeat(4)}end`;
const VIDEOS = Array.from({ length: 12 }, (_, i) => ({
  id: `vid${String(i).padStart(2, "0")}aaaaaa`,
  kind: "video",
  title: i === 0 ? LONG_TITLE : `Clip number ${i}`,
  url: `https://www.youtube.com/watch?v=vid${String(i).padStart(2, "0")}aaaaaa`,
  view_count: 900 * (i + 1),
  duration_string: `${i + 2}:11`,
  upload_date: "20240105",
  channel: "React Channel",
  thumbnail: `https://i.ytimg.com/vi/vid${i}/mqdefault.jpg`,
}));
const PASTE = `${VIDEOS.map((v) => v.id).join("\n")}\n${VIDEOS[0]?.id}\nnot a video`;
const PHASES = { extract: "e1", qdrant: "q1", neo4j: "n1" };

async function mockSource(page: Page) {
  await page.route("https://i.ytimg.com/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#444"/></svg>',
    }),
  );
  const page1 = {
    source: "ids",
    source_id: "",
    total: VIDEOS.length,
    offset: 0,
    limit: 100,
    has_more: false,
    items: VIDEOS,
  };
  await page.route("**/api/v1/ycs/content/videos/preview**", (route) => route.fulfill({ json: page1 }));
  await page.route("**/api/v1/ycs/content/videos/pipeline", (route) =>
    route.fulfill({ json: { phases: PHASES, video_ids: VIDEOS.map((v) => v.id) } }),
  );
}

async function fetchVideos(page: Page) {
  await mockSource(page);
  await page.goto("/ycs");
  await page.getByRole("button", { name: "Videos", exact: true }).click();
  await page.getByLabel("Video IDs or URLs").fill(PASTE);
  await expect(page.getByText("12 videos · 1 duplicate skipped · 1 not recognized")).toBeVisible();
  await page.getByRole("button", { name: "Fetch videos" }).click();
  await expect(page.getByRole("list", { name: "Source videos" }).getByRole("listitem")).toHaveCount(12);
}

test.describe("YouTube Content Search · Source · Videos, Playlist and Channel", () => {
  test("Videos: says what the paste holds, has no ingest bar until videos are listed, selects a Shift range and queues them", async ({
    page,
  }) => {
    await mockSource(page);
    await page.goto("/ycs");
    await page.getByRole("button", { name: "Videos", exact: true }).click();
    await expect(page.getByRole("region", { name: "Ingestion" })).toHaveCount(0);
    await expect(page.getByText(/One ID or URL per line/)).toBeVisible();

    await fetchVideos(page);
    await expect(page.getByRole("region", { name: "Ingestion" })).toBeVisible();
    const master = page.getByRole("checkbox", { name: "Select all loaded videos" });
    await expect(master).not.toBeChecked();
    expect(await master.evaluate((el: HTMLInputElement) => el.indeterminate)).toBe(false);

    await page.getByRole("checkbox", { name: "Select Clip number 1", exact: true }).click();
    await page
      .getByRole("checkbox", { name: "Select Clip number 4", exact: true })
      .click({ modifiers: ["Shift"] });
    await expect(page.getByText(/selected/).first()).toContainText("4 selected");

    await page.getByRole("button", { name: "Start Ingestion (4 videos)" }).click();
    await expect(page).toHaveURL(/\/ycs\/ingestion\?extract=e1&qdrant=q1&neo4j=n1/);
  });

  test("Playlist: a paste that is a channel is offered the Channel tab, and one click moves it there", async ({
    page,
  }) => {
    await page.goto("/ycs");
    await page.getByRole("button", { name: "Playlist", exact: true }).click();
    await page.getByLabel("Playlist", { exact: true }).fill("@anthropicai");
    await expect(page.getByText("This looks like a channel, not a playlist.")).toBeVisible();
    await page.getByRole("button", { name: "Switch to Channel" }).click();

    await expect(page.getByRole("button", { name: "Channel", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByText("@anthropicai")).toBeVisible();
  });

  test("fits the screen and keeps text at 12px or more once videos are listed and some are selected", async ({
    page,
  }) => {
    await fetchVideos(page);
    await page.getByRole("checkbox", { name: "Select Clip number 2", exact: true }).click();
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
    test(`has no WCAG 2.2 AA violations with videos listed and some selected (${scheme})`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await fetchVideos(page);
      await page.getByRole("checkbox", { name: "Select Clip number 2", exact: true }).click();
      const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
      expect(
        results.violations,
        JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)])),
      ).toEqual([]);
    });
  }
});
