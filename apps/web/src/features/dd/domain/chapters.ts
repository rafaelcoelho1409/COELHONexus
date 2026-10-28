/** Docs Distiller synth — chapter cells for the strip, the study list and the run controller. */

import type { SynthChapters } from "../entities";
import { isRecord } from "./format";

/** A readable label from a chapter id: strips the "ch-NN-" prefix, turns separators into spaces and
 * capitalises. The fallback while titles load; the chapters endpoint supplies the exact title. */
export function humanizeChapterId(id: string): string {
  const text = id
    .replace(/^ch[-_]?\d+[-_]?/i, "")
    .replace(/[-_]+/g, " ")
    .trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : id;
}

export type ChapterCell = {
  id: string;
  title: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  wallMs: number | null;
  threadId: string | null;
  selected: boolean;
};

/** Server statuses outside the known set count as done: the chapter finished, just with a status we don't name. */
export function toChapterStatus(status: string): ChapterCell["status"] {
  return status === "failed" || status === "cancelled" ? status : "done";
}

/** Strings out of an open event payload field; anything else is dropped. */
export function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** The chapter titles the planner's `chapter_propose` node chose to propose, read from its stats in the run's graph
 * state. Available as soon as that node is done — before the plan itself is written. */
export function proposedTitles(values: Record<string, unknown>): string[] {
  const stats = values.propose_stats;
  return isRecord(stats) ? stringList(stats.titles) : [];
}

/** Chapter cells from the plan endpoint, in plan order, with the durable render status. */
export function cellsFromPlan(data: SynthChapters): ChapterCell[] {
  return data.chapters
    .slice()
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
    .map((chapter) => ({
      id: chapter.id,
      title: chapter.title || humanizeChapterId(chapter.id),
      status: chapter.rendered ? ("done" as const) : ("pending" as const),
      wallMs: chapter.rendered ? chapter.wall_ms : null,
      threadId: chapter.thread_id ?? null,
      selected: false,
    }));
}

/** Roughly how many characters a person reads in a minute of technical prose. */
const CHARS_PER_READING_MINUTE = 1_000;

/** The facts line under a chapter's title: sections, sources, size and reading time. Only what is known. */
export function chapterFacts(chapter: SynthChapters["chapters"][number]): string[] {
  const facts: string[] = [];
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
  if (chapter.n_sections != null) facts.push(plural(chapter.n_sections, "section"));
  if (chapter.n_sources != null) facts.push(plural(chapter.n_sources, "source"));
  const chars = chapter.rendered_chars;
  if (chars != null && chars > 0) {
    facts.push(`${chars >= 1_000 ? `${(chars / 1_000).toFixed(1)}k` : chars} chars`);
    facts.push(`${Math.max(1, Math.round(chars / CHARS_PER_READING_MINUTE))} min read`);
  }
  return facts;
}

/** Placeholder cells from bare ids (the study run announces ids before titles are known). */
export function cellsFromIds(ids: readonly string[]): ChapterCell[] {
  return ids.map((id) => ({
    id,
    title: humanizeChapterId(id),
    status: "pending" as const,
    wallMs: null,
    threadId: null,
    selected: false,
  }));
}

/** Outcome of the whole-book coherence pass: chapters patched out of those with issues, and whether it came from cache. */
export function harmonizeDoneLabel(patched: number, withIssues: number, cached = false): string {
  const suffix = cached ? " (cache)" : "";
  if (patched > 0) return `${patched}/${withIssues} patched${suffix}`;
  return `${withIssues === 0 ? "Verified, no patches" : `${withIssues} issues, 0 patched`}${suffix}`;
}

export function chapterCounter(cells: readonly ChapterCell[]): string {
  const done = cells.filter((cell) => cell.status === "done").length;
  const failed = cells.filter((cell) => cell.status === "failed" || cell.status === "cancelled").length;
  return failed > 0 ? `${done} done, ${failed} failed / ${cells.length}` : `${done} / ${cells.length}`;
}

/** Partly rendered book → "Resume" (the backend skips rendered chapters), else "Start". */
export function synthStartLabel(cells: readonly ChapterCell[]): "Start" | "Resume" {
  const done = cells.filter((cell) => cell.status === "done").length;
  return done > 0 && done < cells.length ? "Resume" : "Start";
}
