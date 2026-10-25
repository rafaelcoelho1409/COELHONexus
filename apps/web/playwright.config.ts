import { defineConfig, devices } from "@playwright/test";

// e2e runs against the production build (`npm run build` first) on its own port, so it never collides with
// `next dev` on :3000. The API is mocked per test (see e2e/fixtures.ts) — nothing needs to be running.
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `PORT=${PORT} node scripts/serve-standalone.mjs`,
    url: `http://localhost:${PORT}/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
