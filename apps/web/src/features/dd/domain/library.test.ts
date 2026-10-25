import { describe, expect, it } from "vitest";

import type { LibraryItem } from "../entities";
import { deleteCascadeText, itemDisplayName, itemLogos } from "./library";

function item(extra: Partial<LibraryItem>): LibraryItem {
  return {
    slug: "s",
    framework_name: null,
    framework_slug: null,
    page_count: null,
    ingested_at: null,
    tier_kind: null,
    logo: null,
    logos: null,
    ...extra,
  };
}

describe("library helpers", () => {
  it("prefers the logo strip over the single logo", () => {
    expect(itemLogos(item({ logos: ["a.png", "b.png"], logo: "c.png" }))).toEqual(["a.png", "b.png"]);
    expect(itemLogos(item({ logo: "one.png" }))).toEqual(["one.png"]);
  });

  it("names an item by framework name, then framework slug, then slug", () => {
    expect(itemDisplayName(item({ framework_name: "Alpha", framework_slug: "alpha" }))).toBe("Alpha");
    expect(itemDisplayName(item({ framework_slug: "alpha" }))).toBe("alpha");
    expect(itemDisplayName(item({}))).toBe("s");
  });

  it("names the downstream stages a delete would also wipe", () => {
    const state = { ingestion: true, planner: true, synth: true, study: false };
    expect(deleteCascadeText(state, "ingestion")).toBe(" Also wipes its planner and synth state.");
    expect(deleteCascadeText(state, "planner")).toBe(" Also wipes its synth state.");
    expect(deleteCascadeText({}, "ingestion")).toBe("");
    expect(deleteCascadeText(null, "ingestion")).toBe("");
  });
});
