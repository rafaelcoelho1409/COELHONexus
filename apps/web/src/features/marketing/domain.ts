// Plain guards instead of zod: the generated client types these responses as open objects, the panel only
// needs three fields from them, and zod's core cost ~34 KB gz on every visitor's first load (measured).
// Anything larger than this — forms, request bodies, env — validates with zod as usual.

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export function isHealthy(data: unknown): boolean {
  return isRecord(data) && data.status === "healthy";
}

/** Documentation sets that have been ingested (the endpoint returns one row per set). */
export function countDocumentationSets(data: unknown): number | null {
  return Array.isArray(data) ? data.length : null;
}

export function countActiveRuns(data: unknown): number | null {
  return isRecord(data) && Array.isArray(data.active) ? data.active.length : null;
}

export function countIndexedVideos(data: unknown): number | null {
  if (!isRecord(data)) return null;
  const { total } = data;
  return typeof total === "number" && Number.isInteger(total) && total >= 0 ? total : null;
}

const numbers = new Intl.NumberFormat("en-US");

export function formatCount(value: number | null | undefined, placeholder: string): string {
  return value == null ? placeholder : numbers.format(value);
}
