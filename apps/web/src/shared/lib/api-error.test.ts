import { describe, expect, it } from "vitest";

import { apiErrorMessage } from "./api-error";

const NEUTRAL = "Something went wrong. Try again.";

describe("apiErrorMessage", () => {
  it("reads a string detail from the body the generated client throws", () => {
    expect(apiErrorMessage({ detail: "Key is managed by the deployment" })).toBe(
      "Key is managed by the deployment",
    );
  });

  it("reads the first validation message from a detail list", () => {
    expect(apiErrorMessage({ detail: [{ msg: "field required" }, { msg: "later" }] })).toBe("field required");
  });

  it("reads a detail carried as JSON text inside an Error", () => {
    expect(apiErrorMessage(new Error(JSON.stringify({ detail: "slug is locked" })))).toBe("slug is locked");
  });

  it("never leaks raw text, stack traces or odd shapes", () => {
    expect(apiErrorMessage(new Error("TypeError: failed to fetch"))).toBe(NEUTRAL);
    expect(apiErrorMessage(new Error(JSON.stringify({ message: "x" })))).toBe(NEUTRAL);
    expect(apiErrorMessage({ detail: 10 })).toBe(NEUTRAL);
    expect(apiErrorMessage({ detail: [] })).toBe(NEUTRAL);
    expect(apiErrorMessage({ detail: [{ msg: "   " }] })).toBe(NEUTRAL);
    expect(apiErrorMessage({ detail: "  " })).toBe(NEUTRAL);
    expect(apiErrorMessage(null)).toBe(NEUTRAL);
    expect(apiErrorMessage(undefined)).toBe(NEUTRAL);
    expect(apiErrorMessage("boom")).toBe(NEUTRAL);
  });
});
