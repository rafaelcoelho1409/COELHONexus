"use client";

/** "On this page": the headings of the page being read, with the one in view marked. Hidden when there is nothing to
 * navigate (fewer than two headings). */

import type { TocEntry } from "@/shared/markdown";

export function PageOutline({ entries, activeId }: { entries: TocEntry[]; activeId: string | null }) {
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
