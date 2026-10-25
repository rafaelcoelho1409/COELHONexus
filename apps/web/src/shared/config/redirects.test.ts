import { describe, expect, it } from "vitest";

import { buildRedirects } from "./redirects";

describe("buildRedirects", () => {
  it("sends the old Docs Distiller URLs to the new pages, the root and every stage below it", () => {
    expect(buildRedirects()).toEqual([
      { source: "/docs-distiller", destination: "/dd", permanent: false },
      { source: "/docs-distiller/:path*", destination: "/dd/:path*", permanent: false },
    ]);
  });

  it("stays temporary so a rollback to the old UI is not cached away in browsers", () => {
    expect(buildRedirects().every((redirect) => !redirect.permanent)).toBe(true);
  });
});
