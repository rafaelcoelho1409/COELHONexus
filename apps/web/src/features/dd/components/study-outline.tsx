"use client";

/** "On this page": the chapter's h2/h3 headings, with the one being read marked. Hidden when there is nothing
 * to navigate (fewer than two headings). */

import type { TocEntry } from "@/shared/markdown";

export function StudyOutline({ entries, activeId }: { entries: TocEntry[]; activeId: string | null }) {
  if (entries.length < 2) return null;
  return (
    <nav aria-label="On this page" className="text-sm">
      <p className="mb-2 text-xs font-medium text-muted-foreground">On this page</p>
      <ul className="flex flex-col border-l border-border">
        {entries.map((entry) => (
          <li key={entry.id}>
            <a
              href={`#${entry.id}`}
              aria-current={activeId === entry.id ? "location" : undefined}
              className={`-ml-px block min-h-9 border-l-2 py-1.5 pr-2 leading-snug ${
                entry.level === 3 ? "pl-6" : "pl-3"
              } ${
                activeId === entry.id
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {entry.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
