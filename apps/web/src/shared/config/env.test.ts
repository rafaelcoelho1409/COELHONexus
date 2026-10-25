import { describe, expect, it } from "vitest";

import { parseEnv } from "./env";

describe("parseEnv", () => {
  it("falls back to the local API and leaves the optional pieces off", () => {
    expect(parseEnv({})).toEqual({
      SERVER_URL: "http://localhost:23030",
      FASTHTML_URL: undefined,
      EXPOSE_API_DOCS: false,
    });
  });

  it("accepts in-cluster hostnames without a dot and strips trailing slashes", () => {
    const env = parseEnv({
      SERVER_URL: "http://coelhonexus-server:8000/",
      FASTHTML_URL: "http://coelhonexus-fasthtml:3000//",
    });
    expect(env.SERVER_URL).toBe("http://coelhonexus-server:8000");
    expect(env.FASTHTML_URL).toBe("http://coelhonexus-fasthtml:3000");
  });

  it("treats empty strings as unset (an empty Docker build arg must not break the build)", () => {
    expect(parseEnv({ SERVER_URL: "", FASTHTML_URL: "" })).toEqual(parseEnv({}));
  });

  it("parses the docs flag as a boolean", () => {
    expect(parseEnv({ EXPOSE_API_DOCS: "true" }).EXPOSE_API_DOCS).toBe(true);
    expect(parseEnv({ EXPOSE_API_DOCS: "0" }).EXPOSE_API_DOCS).toBe(false);
  });

  it("names every invalid variable in one error", () => {
    expect(() => parseEnv({ SERVER_URL: "not a url", EXPOSE_API_DOCS: "maybe" })).toThrow(
      /SERVER_URL.*EXPOSE_API_DOCS/,
    );
  });
});
