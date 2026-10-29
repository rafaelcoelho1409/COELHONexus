"use client";

/** The chapters of the study as links, the open one marked: the left column of the page and of the reading window. */

import Link from "next/link";

import { humanizeChapterId } from "../domain/chapters";
import { fmtMs } from "../domain/format";
import type { SynthChapters } from "../entities";
import { chapterStudyHref } from "../params";

export function StudyChapterList({
  slug,
  cells,
  currentId,
  totalWallMs,
}: {
  slug: string;
  cells: SynthChapters["chapters"];
  currentId: string | null;
  /** How long the whole study took to write; 0 when unknown. */
  totalWallMs: number;
}) {
  return (
    <nav aria-label="Chapters" className="rounded-md border border-border bg-card">
      <p className="flex items-center justify-between border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
        <span>Chapters</span>
        {totalWallMs > 0 ? <span className="font-mono">{fmtMs(totalWallMs)}</span> : null}
      </p>
      <ul>
        {cells.map((cell, index) => (
          <li key={cell.id}>
            <Link
              href={chapterStudyHref(slug, cell.id)}
              aria-current={currentId === cell.id ? "page" : undefined}
              className={`flex min-h-9 items-center gap-2 px-3 text-sm ${
                currentId === cell.id
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <span className="font-mono text-xs">{index + 1}</span>
              <span className="min-w-0 flex-1 truncate">{cell.title || humanizeChapterId(cell.id)}</span>
              {cell.rendered ? (
                cell.wall_ms ? (
                  <span className="font-mono text-xs">{fmtMs(cell.wall_ms)}</span>
                ) : null
              ) : (
                <span className="text-xs">pending</span>
              )}
            </Link>
          </li>
        ))}
      </ul>
      {cells.length === 0 ? (
        <p className="px-3 py-3 text-sm text-muted-foreground">No chapters yet — run synth first.</p>
      ) : null}
    </nav>
  );
}
