/** Typed reads from the open objects a graph checkpoint holds. Everything is optional there, so every read
 * has a fallback and nothing throws. */

import { isRecord } from "../format";

export type Stats = Record<string, unknown>;

/** A stats dict out of the checkpoint values; empty when absent or not an object. */
export function statsOf(values: Record<string, unknown>, key: string): Stats {
  const value = values[key];
  return isRecord(value) ? value : {};
}

export function num(stats: Stats, key: string, fallback = 0): number {
  const value = stats[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** The number, or undefined when the stats never reported it ("pending" vs a real zero). */
export function maybeNum(stats: Stats, key: string): number | undefined {
  const value = stats[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function text(stats: Stats, key: string, fallback = ""): string {
  const value = stats[key];
  return typeof value === "string" ? value : fallback;
}

/** `true` / `false` as reported, undefined when the key is missing or not a boolean. */
export function flag(stats: Stats, key: string): boolean | undefined {
  const value = stats[key];
  return typeof value === "boolean" ? value : undefined;
}

function list(stats: Stats, key: string): unknown[] {
  const value = stats[key];
  return Array.isArray(value) ? value : [];
}

export function texts(stats: Stats, key: string): string[] {
  return list(stats, key).filter((item): item is string => typeof item === "string");
}

export function records(stats: Stats, key: string): Stats[] {
  return list(stats, key).filter(isRecord);
}

/** `12 345`-style thousands separators. */
export function int(n: number): string {
  return n.toLocaleString("en-US");
}

export function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/** The last `count` path segments, for page keys like `ingest/slug/docs/page.md`. */
export function pathTail(path: string, count = 1): string {
  return path.split("/").slice(-count).join("/");
}
