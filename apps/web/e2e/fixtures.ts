import { test as base } from "@playwright/test";

type ApiMode = "healthy" | "down";

/** Every spec gets a deterministic API: the page never reaches a real backend. */
export const test = base.extend<{ api: (mode: ApiMode) => Promise<void> }>({
  // The fixture callback is named `provide` (Playwright's docs call it `use`): react-hooks/rules-of-hooks
  // mistakes anything called `use(...)` for a React hook.
  api: async ({ page }, provide) => {
    await provide(async (mode) => {
      await page.route("**/api/**", (route) => {
        if (mode === "down") return route.fulfill({ status: 503, body: "" });
        const path = new URL(route.request().url()).pathname;
        const json = (body: unknown) => route.fulfill({ json: body });
        if (path === "/api/health") return json({ status: "healthy" });
        if (path === "/api/v1/dd/ingestion") return json([{ slug: "a" }, { slug: "b" }, { slug: "c" }]);
        if (path === "/api/v1/ycs/admin/videos") return json({ items: [], total: 1204 });
        if (path === "/api/v1/dd/runs/active") return json({ active: [] });
        return route.fulfill({ status: 404, body: "" });
      });
    });
  },
});

export { expect } from "@playwright/test";
