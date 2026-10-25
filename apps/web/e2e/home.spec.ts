import AxeBuilder from "@axe-core/playwright";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

test.describe("home", () => {
  test("renders the product story and live platform figures", async ({ page, api }) => {
    await api("healthy");
    await page.goto("/");

    await expect(page.getByRole("heading", { level: 1 })).toContainText("Framework docs, distilled");
    for (const section of [
      "Two tools, one platform.",
      "Six layers",
      "How it is built.",
      "Run it yourself.",
    ]) {
      await expect(page.getByRole("heading", { level: 2, name: new RegExp(section) })).toBeVisible();
    }
    const panel = page.getByRole("region", { name: "Platform" });
    await expect(panel.getByText("API operational")).toBeVisible();
    await expect(panel.getByText("1,204")).toBeVisible();
  });

  test("says so when the API is unreachable", async ({ page, api }) => {
    await api("down");
    await page.goto("/");
    const panel = page.getByRole("region", { name: "Platform" });
    await expect(panel.getByText("API unreachable")).toBeVisible();
  });

  for (const scheme of ["dark", "light"] as const) {
    test(`has no WCAG 2.2 AA violations (${scheme})`, async ({ page, api }) => {
      await api("healthy");
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/");
      await expect(page.getByText("API operational")).toBeVisible();

      const { violations } = await new AxeBuilder({ page }).withTags(WCAG).analyze();
      expect(violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual(
        [],
      );
    });
  }

  test("skip link is the first stop and lands on the content", async ({ page, api }) => {
    await api("healthy");
    await page.goto("/");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#main$/);
  });

  test("theme follows the OS, can be overridden, and survives a reload without a flash", async ({
    page,
    api,
  }) => {
    await api("healthy");
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    // The inline bootstrap sets the attribute before hydration — no flash of the wrong theme.
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    const toggle = page.getByRole("button", { name: /^Theme:/ });
    await expect(toggle).toHaveAccessibleName("Theme: System. Switch to Light.");
    await toggle.click(); // system → light
    await toggle.click(); // light → dark
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.getByRole("button", { name: /^Theme: Dark/ })).toBeVisible();
  });

  test("does not scroll sideways", async ({ page, api }) => {
    await api("healthy");
    await page.goto("/");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("never ships text under 12px", async ({ page, api }) => {
    await api("healthy");
    await page.goto("/");
    const tooSmall = await page.evaluate(() => {
      const small: string[] = [];
      for (const el of document.body.querySelectorAll("*")) {
        const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent?.trim());
        if (hasText && Number.parseFloat(getComputedStyle(el).fontSize) < 12) small.push(el.tagName);
      }
      return small;
    });
    expect(tooSmall).toEqual([]);
  });
});

test("health probe answers for the cluster", async ({ request }) => {
  const response = await request.get("/health");
  expect(response.ok()).toBe(true);
});
