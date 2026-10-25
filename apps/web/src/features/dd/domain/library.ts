/** Docs Distiller library — display names and the delete-cascade copy. */

import type { LibraryItem } from "../entities";
import { isRecord } from "./format";

/** Prefer the explicit multi-logo strip over the single logo. */
export function itemLogos(item: LibraryItem): string[] {
  if (item.logos && item.logos.length > 0) return item.logos;
  return item.logo ? [item.logo] : [];
}

export function itemDisplayName(item: LibraryItem): string {
  return item.framework_name || item.framework_slug || item.slug;
}

/** Delete-confirm copy: names the downstream stages that exist and would be wiped too. */
export function deleteCascadeText(state: unknown, from: string): string {
  const downstream = isRecord(state)
    ? ["planner", "synth", "study"].filter((stage) => stage !== from && state[stage] === true)
    : [];
  return downstream.length > 0 ? ` Also wipes its ${downstream.join(" and ")} state.` : "";
}
