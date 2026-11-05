/** Words for what the Agentic AI step found on a page (`GET …/pages/{n}/quality`). The server sends codes; the reader sees
 * a label, a count and a sentence, never a raw code alone. */

import type { PageQuality } from "../entities";

const ISSUE_LABELS: Record<string, string> = {
  control_characters: "Control characters",
  soft_hyphen: "Soft hyphens",
  split_word: "Split word",
  fused_marker: "Fused marker",
  text_corruption: "Damaged text",
  toc_noise: "Contents formatting",
  table_or_index_layout: "Table or index layout",
  structure: "Structure",
  near_empty: "Almost empty page",
  metadata: "Metadata",
  other: "Other",
};

export const RESOLUTION_LABELS = {
  auto_applied: "Fixed automatically",
  review: "Needs review",
  none: "Noted",
} as const;

const sentenceCase = (code: string) => {
  const words = code.replaceAll("_", " ").trim();
  return words ? `${words[0]?.toUpperCase()}${words.slice(1)}` : code;
};

export const issueLabel = (code: string): string => ISSUE_LABELS[code] ?? sentenceCase(code);

const FIX_NOUNS: Record<string, [string, string]> = {
  control_characters: ["control character", "control characters"],
  soft_hyphen: ["soft hyphen", "soft hyphens"],
};

/** "Removed 2 control characters and 1 soft hyphen automatically.", or null when nothing was removed. */
export function describeMechanicalFixes(fixes: Readonly<Record<string, number>>): string | null {
  const removed = Object.entries(fixes)
    .filter(([, count]) => count > 0)
    .filter(([code]) => code !== "table_cleanup")
    .map(([code, count]) => {
      const [one, many] = FIX_NOUNS[code] ?? [code.replaceAll("_", " "), code.replaceAll("_", " ")];
      return `${count} ${count === 1 ? one : many}`;
    });
  const sentences: string[] = [];
  if (removed.length > 0) {
    const list =
      removed.length === 1 ? (removed[0] ?? "") : `${removed.slice(0, -1).join(", ")} and ${removed.at(-1)}`;
    sentences.push(`Removed ${list} automatically.`);
  }
  const tables = fixes.table_cleanup ?? 0;
  if (tables > 0) sentences.push(`Cleaned up ${tables} ${tables === 1 ? "table" : "tables"} automatically.`);
  return sentences.length ? sentences.join(" ") : null;
}

export function issueCounts(quality: PageQuality): { review: number; fixed: number; noted: number } {
  const count = (resolution: PageQuality["issues"][number]["resolution"]) =>
    quality.issues.filter((issue) => issue.resolution === resolution).length;
  return { review: count("review"), fixed: count("auto_applied"), noted: count("none") };
}

/** Why a page is not part of the delivered text, or null for an ordinary page. */
export function dispositionNote(quality: PageQuality): string | null {
  if (quality.delivery_disposition === "discarded_blank") {
    return "Blank page: kept as page numbering, left out of the delivered text.";
  }
  if (quality.delivery_disposition === "image_only") {
    return "Image-only page: it has pictures but no readable text.";
  }
  return null;
}
