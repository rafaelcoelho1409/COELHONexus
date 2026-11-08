import type { PageQualityReport } from "./types";

const ISSUE_LABELS: Record<string, string> = {
  control_characters: "Control characters",
  soft_hyphen: "Soft hyphens",
  split_word: "Split word",
  fused_marker: "Fused marker",
  text_corruption: "Damaged text",
  toc_noise: "Contents formatting",
  table_or_index_layout: "Table or index layout",
  duplicate_boilerplate: "Repeated boilerplate",
  duplicate_section: "Repeated section",
  malformed_heading: "Malformed heading",
  markdown_formatting: "Markdown formatting",
  generated_title: "Generated title",
  encoding_artifact: "Encoding artifact",
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
  non_whitespace_control_chars_stripped: [
    "non-whitespace control character",
    "non-whitespace control characters",
  ],
  soft_hyphen: ["soft hyphen", "soft hyphens"],
  zero_width_chars_stripped: ["zero-width character", "zero-width characters"],
  agent_banner_stripped: ["agent banner", "agent banners"],
  agent_footer_stripped: ["agent footer", "agent footers"],
  footer_links_stripped: ["footer link", "footer links"],
  code_annotations_stripped: ["code annotation", "code annotations"],
  code_copy_lines_stripped: ["code copy line", "code copy lines"],
  fence_meta_stripped: ["code fence attribute", "code fence attributes"],
  boundary_markers_stripped: ["boundary marker", "boundary markers"],
  orphan_tags_stripped: ["orphan tag", "orphan tags"],
  permalink_debris_stripped: ["permalink artifact", "permalink artifacts"],
  heading_anchors_stripped: ["heading anchor", "heading anchors"],
  viewcode_links_stripped: ["source link", "source links"],
  toc_tables_stripped: ["table of contents table", "table of contents tables"],
  empty_media_stripped: ["empty media block", "empty media blocks"],
  trailing_rules_stripped: ["trailing rule", "trailing rules"],
  license_header_stripped: ["license header", "license headers"],
  frontmatter_extracted: ["frontmatter block", "frontmatter blocks"],
  html_entities_decoded: ["HTML entity", "HTML entities"],
  blank_lines_collapsed: ["extra blank line", "extra blank lines"],
  trailing_ws_lines: ["line with trailing whitespace", "lines with trailing whitespace"],
  table_cleanup: ["table", "tables"],
};

/** Describe deterministic cleanup without exposing internal counter names. */
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

export function issueCounts(quality: PageQualityReport): { review: number; fixed: number; noted: number } {
  const count = (resolution: PageQualityReport["issues"][number]["resolution"]) =>
    quality.issues.filter((issue) => issue.resolution === resolution).length;
  return { review: count("review"), fixed: count("auto_applied"), noted: count("none") };
}

/** Why the page is not part of the delivered text, or null for an ordinary page. */
export function dispositionNote(quality: PageQualityReport): string | null {
  if (quality.delivery_disposition === "discarded_blank") {
    return "Blank page: kept as page numbering, left out of the delivered text.";
  }
  if (quality.delivery_disposition === "image_only") {
    return "Image-only page: it has pictures but no readable text.";
  }
  return null;
}
