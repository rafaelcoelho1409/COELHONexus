"use client";

/** Search every rendered chapter of the framework (opened with Ctrl/Cmd+K or the Search button): chapter
 * titles first, then headings, then matches in the text. A combobox: focus stays in the field while the arrow
 * keys move through the results. */

import { useId, useMemo, useState } from "react";

import { type SearchHit, searchChapters } from "../domain/study-search";
import { useChapterIndex } from "../hooks/use-chapter-index";
import { useModalDialog } from "../hooks/use-modal-dialog";

export function StudySearch({
  slug,
  chapters,
  onChoose,
  onClose,
}: {
  slug: string;
  chapters: ReadonlyArray<{ id: string; title: string; rendered: boolean }>;
  onChoose: (hit: SearchHit) => void;
  onClose: () => void;
}) {
  const ref = useModalDialog();
  const titleId = useId();
  const listId = useId();
  const { entries, loading } = useChapterIndex(slug, chapters, true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);

  const hits = useMemo(() => searchChapters(entries, query), [entries, query]);
  const active = hits.length === 0 ? -1 : Math.min(selected, hits.length - 1);
  const optionId = (index: number) => `${listId}-${index}`;

  const status = loading
    ? "Indexing chapters…"
    : query.trim() === ""
      ? "Type to search every chapter."
      : hits.length === 0
        ? "No matches."
        : `${hits.length} result${hits.length === 1 ? "" : "s"}`;

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="mx-auto mt-[12vh] mb-auto w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-md border border-border bg-card p-0 text-foreground backdrop:bg-background/80"
    >
      <h2 id={titleId} className="sr-only">
        Search all chapters
      </h2>
      <input
        type="search"
        autoFocus
        role="combobox"
        aria-expanded={hits.length > 0}
        aria-controls={listId}
        aria-activedescendant={active >= 0 ? optionId(active) : undefined}
        aria-autocomplete="list"
        aria-label="Search all chapters"
        autoComplete="off"
        spellCheck={false}
        placeholder="Search all chapters…"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setSelected(0);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setSelected(Math.min(active + 1, hits.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setSelected(Math.max(active - 1, 0));
          } else if (event.key === "Enter") {
            event.preventDefault();
            const hit = hits[active];
            if (hit) onChoose(hit);
          }
        }}
        className="h-12 w-full border-b border-border bg-transparent px-4 text-sm outline-none"
      />
      <div id={listId} role="listbox" aria-label="Results" className="max-h-80 overflow-y-auto">
        {hits.map((hit, index) => (
          <button
            key={`${hit.chapterId}:${hit.headingId ?? hit.sub}`}
            type="button"
            id={optionId(index)}
            role="option"
            tabIndex={-1}
            aria-selected={index === active}
            onClick={() => onChoose(hit)}
            className={`flex min-h-9 w-full items-baseline gap-3 px-4 py-2 text-left text-sm ${
              index === active ? "bg-muted" : "hover:bg-muted"
            }`}
          >
            <span className="font-medium">{hit.title}</span>
            {hit.sub !== hit.title ? <span className="text-muted-foreground">{hit.sub}</span> : null}
          </button>
        ))}
      </div>
      <p role="status" className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
        {status}
      </p>
    </dialog>
  );
}
