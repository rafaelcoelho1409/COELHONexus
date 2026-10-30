import { expect, test } from "@playwright/test";

// The old UI's Docs Distiller URLs now belong to this app. Following them must land on the same page
// with the same query, and the redirect must stay temporary so it can be rolled back.
test.describe("legacy Docs Distiller URLs", () => {
  const cases = [
    ["/docs-distiller", "/dd"],
    ["/docs-distiller/ingestion?slug=fasthtml&run=r1", "/dd/ingestion?slug=fasthtml&run=r1"],
    ["/docs-distiller/planner?slug=fasthtml", "/dd/planner?slug=fasthtml"],
    ["/docs-distiller/synth?slug=fasthtml", "/dd/synth?slug=fasthtml"],
    ["/docs-distiller/pipeline?slug=fasthtml", "/dd/pipeline?slug=fasthtml"],
    ["/docs-distiller/study?slug=fasthtml&chapter=01_intro", "/dd/study?slug=fasthtml&chapter=01_intro"],
  ] as const;

  for (const [from, to] of cases) {
    test(`${from} redirects (307) to ${to}`, async ({ request }) => {
      const response = await request.get(from, { maxRedirects: 0 });
      expect(response.status()).toBe(307);
      expect(response.headers().location).toBe(to);
    });
  }
});
