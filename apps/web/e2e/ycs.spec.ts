import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

// Real search results carry long titles, unbroken descriptions and many kinds: the page must still fit the screen.
const LONG_TITLE = `${"A very long video title that keeps going and going ".repeat(4)}end`;
const UNBROKEN = "https://example.test/".concat("a-path-without-any-break-".repeat(12));
const RESULTS = [
  ...Array.from({ length: 5 }, (_, i) => ({
    id: `vid${i}`,
    kind: "video",
    title: i === 0 ? LONG_TITLE : `Clip number ${i}`,
    url: `https://www.youtube.com/watch?v=vid${i}`,
    view_count: 1200 * (i + 1),
    duration_string: `${i + 3}:07`,
    channel: "React Channel",
    upload_date: "20240105",
    thumbnail: `https://i.ytimg.com/vi/vid${i}/mqdefault.jpg`,
    description: i === 1 ? UNBROKEN : "Hooks, state and effects explained step by step.",
  })),
  {
    id: "UCchan",
    kind: "channel",
    title: "React Channel",
    url: "https://www.youtube.com/@react",
    video_count: 42,
  },
  {
    id: "PLlist",
    kind: "playlist",
    title: "Hooks playlist",
    url: "https://www.youtube.com/playlist?list=PLlist",
    video_count: 7,
  },
];

async function mockSearch(page: Page) {
  await page.route("https://i.ytimg.com/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#444"/></svg>',
    }),
  );
  await page.route("**/api/v1/ycs/content/search", (route) =>
    route.fulfill({
      json: {
        query: "hooks",
        total: RESULTS.length,
        results: RESULTS,
        fetched_for: RESULTS.length,
        elapsed_s: 1.4,
      },
    }),
  );
}

async function search(page: Page) {
  await mockSearch(page);
  await page.goto("/ycs");
  await page.getByLabel("Search", { exact: true }).fill("hooks");
  await page.getByRole("button", { name: "More filters" }).click();
  await page.getByLabel("Exclude shorts").check();
  await page.getByLabel("Min views").fill("10000");
  // Searched from the foot of the panel: on a phone the panel is a sheet over the page and the Search button is behind it.
  await page.getByRole("button", { name: "Apply and search" }).click();
  await expect(page.getByText(/1–7 of 7 · 1\.4 s/)).toBeVisible();
}

test.describe("YouTube Content Search · Source · Search", () => {
  test("searches, shows the filters as chips, selects a Shift range and routes the videos on", async ({
    page,
  }) => {
    await search(page);

    const chips = page.getByRole("list", { name: "Active filters" });
    await expect(chips.getByRole("button", { name: "Remove filter: Min views: 10,000" })).toBeVisible();
    await expect(chips.getByRole("button", { name: "Remove filter: No shorts" })).toBeVisible();

    const rows = page.getByRole("list", { name: "Search results" }).getByRole("listitem");
    await expect(rows).toHaveCount(7);
    await expect(rows.nth(5)).toContainText("Channel");
    await expect(rows.nth(6)).toContainText("7 videos");

    await page.getByRole("checkbox", { name: "Select Clip number 1" }).click();
    await page.getByRole("checkbox", { name: "Select Clip number 3" }).click({ modifiers: ["Shift"] });
    await expect(page.getByRole("region", { name: "Selection" })).toContainText("3 selected");

    await page.getByRole("button", { name: "→ 3 Videos" }).click();
    await expect(page.getByRole("button", { name: "Videos", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  test("the quick filters are on the page, and the panel is in the page on a desktop and a sheet on a phone", async ({
    page,
  }, testInfo) => {
    await mockSearch(page);
    await page.goto("/ycs");
    await expect(page.getByRole("radio", { name: "Channels" })).toBeAttached();
    await expect(page.getByLabel("Duration")).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Newest first" })).toBeVisible();
    await expect(page.getByRole("dialog", { name: "More filters" })).toBeHidden();

    await page.getByRole("button", { name: "More filters" }).click();
    const panel = page.getByRole("dialog", { name: "More filters" });
    await expect(panel).toBeVisible();
    const modal = await panel.evaluate((element) => element.matches(":modal"));
    const box = await panel.boundingBox();
    const viewport = page.viewportSize();
    if (!box || !viewport) throw new Error("no box");
    if (testInfo.project.name === "desktop") {
      expect(modal).toBe(false); // laid out in the page, beside nothing it covers
      expect(box.y + box.height).toBeLessThan(viewport.height);
    } else {
      expect(modal).toBe(true);
      expect(Math.round(box.y + box.height)).toBe(viewport.height); // sits on the bottom edge
      await page.keyboard.press("Escape");
      await expect(panel).toBeHidden();
    }
  });

  test("the results bar stays under the app header while a page scrolls, and / jumps to the query", async ({
    page,
  }, testInfo) => {
    await search(page);
    if (testInfo.project.name === "desktop") {
      await page.evaluate(() => window.scrollTo(0, 400));
      const bar = page.getByRole("checkbox", { name: "Select all visible results" });
      const top = (await bar.boundingBox())?.y ?? -1;
      expect(top).toBeGreaterThanOrEqual(56); // below the 56px app header, not scrolled away
      expect(top).toBeLessThan(140);
    }
    await page.getByRole("heading", { level: 1 }).click();
    await page.keyboard.press("/");
    await expect(page.getByLabel("Search", { exact: true })).toBeFocused();
  });

  test("fits the screen and keeps text at 12px or more, with filters open and rows selected", async ({
    page,
  }) => {
    await search(page);
    await page.getByRole("checkbox", { name: "Select Clip number 1" }).click();
    await page.getByRole("button", { name: "Comfortable" }).click();
    await page.getByRole("button", { name: "More filters" }).click(); // last: on a phone it covers the page
    await expect(page.getByRole("dialog", { name: "More filters" })).toBeVisible();
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
    test(`has no WCAG 2.2 AA violations with filters open, chips shown and rows selected (${scheme})`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await search(page);
      await page.getByRole("checkbox", { name: "Select Clip number 1" }).click();
      await expect(page.getByRole("region", { name: "Selection" })).toBeVisible();
      await page.getByRole("button", { name: "More filters" }).click();
      await expect(page.getByRole("dialog", { name: "More filters" })).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
      expect(
        results.violations,
        JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)])),
      ).toEqual([]);
    });
  }
});
