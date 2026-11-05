/** The Ingestion tab's Materials reader: how one ingested page is named, described and reached from its neighbours. */

import type { Root } from "mdast";
import { toString as plainText } from "mdast-util-to-string";
import type { Plugin } from "unified";

import type { ManifestEntry } from "../entities";
import { fmtBytes } from "./format";

/** The page's heading (the backend fills it); the slug, then its number, only for a manifest that has neither. */
export function pageTitle(entry: ManifestEntry): string {
  return entry.title?.trim() || entry.slug || `Page ${entry.idx + 1}`;
}

/** What is worth saying about a page under its title: how it was fetched, how big it is, the section it sits in. */
export function pageFacts(entry: ManifestEntry): string[] {
  return [entry.tier, fmtBytes(entry.bytes ?? 0), entry.section?.trim()].filter(
    (fact): fact is string => typeof fact === "string" && fact !== "",
  );
}

/** Where the page came from on the web, when it is a web address: the section's own `Source:` URL, else the page's. */
export function sourceHref(entry: ManifestEntry): string | null {
  for (const candidate of [entry.source_path, entry.url]) {
    if (!candidate) continue;
    try {
      const url = new URL(candidate);
      if (url.protocol === "https:" || url.protocol === "http:") return url.href;
    } catch {
      // not an address: try the next
    }
  }
  return null;
}

export type PageNeighbours = {
  /** Where the page is in `list`; -1 when a filter has taken it out. */
  position: number;
  previous: number | null;
  next: number | null;
};

/** The `idx` of the page before and after `idx` in `list` (the pages on show). A page the filter took out has no
 * previous one, and its next is the first page on show. */
export function pageNeighbours(list: readonly ManifestEntry[], idx: number | null): PageNeighbours {
  const position = idx === null ? -1 : list.findIndex((entry) => entry.idx === idx);
  if (position < 0) return { position, previous: null, next: list[0]?.idx ?? null };
  return {
    position,
    previous: list[position - 1]?.idx ?? null,
    next: list[position + 1]?.idx ?? null,
  };
}

const normalized = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();

/** The reader prints the page's title above its text, so a leading `# Title` that says the same would read twice. A heading
 * that says something else stays. */
export function dropLeadingTitle(title: string): Plugin<[], Root> {
  const wanted = normalized(title);
  return () => (tree) => {
    const first = tree.children[0];
    if (first?.type === "heading" && first.depth === 1 && normalized(plainText(first)) === wanted) {
      tree.children = tree.children.slice(1);
    }
  };
}
