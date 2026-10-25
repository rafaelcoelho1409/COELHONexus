import AxeBuilder from "@axe-core/playwright";
import type { Page, Route } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const LLM_GET = {
  url: "http://llm-gateway:8000/v1",
  model: "auto",
  has_key: false,
  source: null,
  last4: null,
};
const EMB_GET = {
  url: "http://emb-gateway:8000/v1",
  model: "e5-v2",
  has_key: true,
  source: "user",
  last4: "abcd",
};

type Phase = "hidden" | "needed" | "running";

/**
 * The API contract lives only in this spec: the brief replaces `api(...)` with explicit handlers, so
 * every request the page makes is decided right here.
 */
async function mockRoutes(page: Page, options?: { status?: () => Phase; task?: unknown[] }) {
  const statusState = options?.status ?? (() => "hidden" as Phase);
  const taskResponses = options?.task ?? [];
  let taskCall = 0;

  const json = (route: Route, body: unknown) => route.fulfill({ json: body });

  await page.route("**/api/v1/settings/endpoint", (route) =>
    route.request().method() === "GET" ? json(route, LLM_GET) : route.continue(),
  );
  await page.route("**/api/v1/settings/embedding", (route) =>
    route.request().method() === "GET" ? json(route, EMB_GET) : route.continue(),
  );
  await page.route("**/api/v1/ycs/content/embedding-migration/status", (route) =>
    json(route, {
      needed: statusState() === "needed",
      mismatch: statusState() === "hidden" ? null : { from_model: "integral-a", to_model: "integral-b" },
      state:
        statusState() === "running"
          ? { status: "running", from_model: "integral-a", to_model: "integral-b", task_id: "task-77" }
          : null,
      active_collection: "ycs-transcripts-2026-10",
    }),
  );
  await page.route("**/api/v1/ycs/admin/task/task-77", (route) => {
    const next = taskResponses[Math.min(taskCall, taskResponses.length - 1)] ?? {
      task_id: "task-77",
      state: "SUCCESS",
    };
    taskCall += 1;
    return json(route, next);
  });
}

test.describe("settings", () => {
  test("renders both cards from the API and never echoes a key", async ({ page }) => {
    await mockRoutes(page);
    await page.goto("/settings");

    await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();

    const llm = page.getByRole("region", { name: "LLM Endpoint" });
    await expect(llm.getByLabel("Base URL")).toHaveValue("http://llm-gateway:8000/v1");
    await expect(llm.getByLabel("API key")).toHaveValue("");
    await expect(llm.getByLabel("Model")).toHaveValue("auto");

    const emb = page.getByRole("region", { name: "Embedding" });
    await expect(emb.getByLabel("Base URL")).toHaveValue("http://emb-gateway:8000/v1");
    await expect(emb.getByLabel("Model")).toHaveValue("e5-v2");
    await expect(emb.getByLabel("API key")).toHaveValue("");
    await expect(emb.getByText("Custom key ••••abcd")).toBeVisible();
    await expect(emb.getByText("ycs-transcripts-2026-10")).toBeVisible();
  });

  test("saving LLM settings sends the exact JSON and clears the key field", async ({ page }) => {
    let saved: object | undefined;
    await mockRoutes(page);
    await page.route("**/api/v1/settings/endpoint", async (route) => {
      if (route.request().method() === "PUT") {
        saved = await route.request().postDataJSON();
        return route.fulfill({
          json: { ...LLM_GET, has_key: true, source: "user", last4: "c1d2" },
        });
      }
      return route.fulfill({ json: LLM_GET });
    });
    await page.goto("/settings");

    const llm = page.getByRole("region", { name: "LLM Endpoint" });
    await expect(llm.getByLabel("Base URL")).toBeEditable();
    await llm.getByLabel("API key").fill("sk-livesafe");
    await llm.getByRole("button", { name: "Save" }).click();

    await expect(llm.getByText("Saved.")).toBeVisible();
    expect(saved).toEqual({
      url: "http://llm-gateway:8000/v1",
      model: "auto",
      api_key: "sk-livesafe",
    });
    await expect(llm.getByLabel("API key")).toHaveValue("");
  });

  test('Remove key sends api_key ""', async ({ page }) => {
    let saved: object | undefined;
    await mockRoutes(page);
    await page.route("**/api/v1/settings/embedding", async (route) => {
      if (route.request().method() === "PUT") {
        saved = await route.request().postDataJSON();
        return route.fulfill({ json: { ...EMB_GET, has_key: false, source: null, last4: null } });
      }
      return route.fulfill({ json: EMB_GET });
    });
    await page.goto("/settings");

    const emb = page.getByRole("region", { name: "Embedding" });
    await expect(emb.getByText("Custom key ••••abcd")).toBeVisible();
    await emb.getByRole("button", { name: "Remove key" }).click();

    await expect(emb.getByText("Saved.")).toBeVisible();
    expect(saved).toEqual({ url: "http://emb-gateway:8000/v1", model: "e5-v2", api_key: "" });
  });

  test("Test reads the saved configuration and prints the result", async ({ page }) => {
    let tested = false;
    await mockRoutes(page);
    await page.route("**/api/v1/settings/embedding/test", (route) => {
      tested = true;
      return route.fulfill({
        json: { ok: true, dimensions: 1024, latency_ms: 87, model: "e5-v2" },
      });
    });
    await page.goto("/settings");

    const emb = page.getByRole("region", { name: "Embedding" });
    await emb.getByLabel("Base URL").fill("http://emb-gateway:8000/changed");
    await emb.getByRole("button", { name: "Test" }).click();

    expect(tested).toBe(true);
    await expect(emb.getByText("OK — 1024 dimensions · e5-v2 · 87 ms")).toBeVisible();
    await expect(emb.getByText("Unsaved changes — Test uses the saved settings.")).toBeVisible();
  });

  test("the migration banner asks, runs, and completes", async ({ page }) => {
    test.setTimeout(60_000);
    let phase: Phase = "needed";
    await mockRoutes(page, {
      status: () => phase,
      task: [
        { task_id: "task-77", state: "PROGRESS", meta: { current: 3, total: 10 } },
        { task_id: "task-77", state: "SUCCESS" },
      ],
    });
    await page.route("**/api/v1/ycs/content/embedding-migration/start", (route) => {
      phase = "running";
      return route.fulfill({ json: {} });
    });
    await page.goto("/settings");

    const emb = page.getByRole("region", { name: "Embedding" });
    const bannerButton = emb.getByRole("button", { name: "Migrate now" });
    await expect(bannerButton).toBeVisible();
    await expect(emb.getByText(/Embedding model changed from/)).toBeVisible();

    await bannerButton.click();
    await expect(emb.getByText(/Migrating integral-a → integral-b/)).toBeVisible();
    await expect(emb.getByText("3/10 transcripts re-embedded")).toBeVisible();
    await expect(emb.getByText("Embedding migration complete")).toBeVisible();
    await expect(emb.getByRole("button", { name: "Migrate now" })).toHaveCount(0);
  });

  test("the whole LLM form completes with keyboard only", async ({ page }) => {
    let saved: object | undefined;
    await mockRoutes(page);
    await page.route("**/api/v1/settings/endpoint", async (route) => {
      if (route.request().method() === "PUT") {
        saved = await route.request().postDataJSON();
        return route.fulfill({ json: LLM_GET });
      }
      return route.fulfill({ json: LLM_GET });
    });
    await page.goto("/settings");

    const llm = page.getByRole("region", { name: "LLM Endpoint" });
    const urlField = llm.getByLabel("Base URL");
    while ((await page.evaluate(() => document.activeElement?.id)) !== "connection-llm-url") {
      await page.keyboard.press("Tab");
    }
    await urlField.fill("http://keyboard:9/v1");
    await page.keyboard.press("Tab");
    await llm.getByLabel("API key").fill("sk-from-keyboard");
    await page.keyboard.press("Tab");
    await llm.getByLabel("Model").fill("kb-1");
    while ((await page.evaluate(() => document.activeElement?.textContent)) !== "Save") {
      await page.keyboard.press("Tab");
    }
    await page.keyboard.press("Enter");

    await expect(llm.getByText("Saved.")).toBeVisible();
    expect(saved).toEqual({ url: "http://keyboard:9/v1", model: "kb-1", api_key: "sk-from-keyboard" });
  });

  test.describe("phone", () => {
    test.use({ viewport: { width: 375, height: 800 } });

    test("text has no horizontal scroll at 375px", async ({ page }) => {
      await mockRoutes(page);
      await page.goto("/settings");
      await expect(page.getByRole("region", { name: "Embedding" }).getByLabel("Model")).toHaveValue("e5-v2");

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });
  });

  for (const scheme of ["dark", "light"] as const) {
    test(`has no WCAG 2.2 AA violations (${scheme})`, async ({ page }) => {
      await mockRoutes(page);
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/settings");
      await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Embedding" }).getByLabel("Model")).toHaveValue("e5-v2");

      const { violations } = await new AxeBuilder({ page }).withTags(WCAG).analyze();
      expect(violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual(
        [],
      );
    });
  }
});
