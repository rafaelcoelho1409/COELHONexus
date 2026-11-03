import { activeChapterIndex, activeEntryIndex, type ChapterNode } from "../domain/outline";

/** The chapters with their outline entries, from the `started` event. The chapter being read is marked (and says so
 * to a screen reader); an entry can be opened once its page has been extracted. */
export function ChapterTree({
  tree,
  activePage,
  unitLabel,
  isExtracted,
  onOpenPage,
}: {
  tree: readonly ChapterNode[];
  activePage: number | null;
  unitLabel: "page" | "section";
  isExtracted: (page: number) => boolean;
  onOpenPage: (page: number) => void;
}) {
  const activeChapter = activeChapterIndex(tree, activePage);
  return (
    <section aria-labelledby="chapters-title">
      <h3 id="chapters-title" className="mono-label">
        Chapters
      </h3>
      {tree.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">This book has no outline.</p>
      ) : (
        <ol className="mt-2 max-h-64 overflow-auto">
          {tree.map((chapter, index) => {
            const current = index === activeChapter;
            const activeEntry = current ? activeEntryIndex(chapter, activePage) : -1;
            return (
              <li key={`${chapter.firstPage}-${chapter.title}`} className="mb-1">
                <button
                  type="button"
                  disabled={!isExtracted(chapter.firstPage)}
                  aria-current={current ? "true" : undefined}
                  onClick={() => onOpenPage(chapter.firstPage)}
                  className={`flex min-h-9 w-full items-baseline gap-2 border-l-2 px-2 text-left text-sm disabled:text-muted-foreground ${
                    current ? "border-primary bg-muted font-medium" : "border-transparent hover:bg-muted"
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">{chapter.title}</span>{" "}
                  <span className="tabular shrink-0 text-xs text-muted-foreground">
                    {unitLabel === "page" ? `p. ${chapter.firstPage}` : `Section ${chapter.firstPage}`}
                  </span>
                </button>
                {chapter.entries.length > 0 ? (
                  <ul>
                    {chapter.entries.map((entry, entryIndex) => (
                      <li key={`${entry.page}-${entry.level}-${entry.title}`}>
                        <button
                          type="button"
                          disabled={!isExtracted(entry.page)}
                          aria-current={entryIndex === activeEntry ? "location" : undefined}
                          onClick={() => onOpenPage(entry.page)}
                          style={{ paddingInlineStart: `${0.5 + Math.max(entry.level - 1, 0) * 0.75}rem` }}
                          className={`flex min-h-9 w-full items-baseline gap-2 pr-2 text-left text-xs disabled:text-muted-foreground ${
                            entryIndex === activeEntry
                              ? "font-medium text-foreground"
                              : "text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          <span className="min-w-0 flex-1 truncate">{entry.title}</span>{" "}
                          <span className="tabular shrink-0">
                            {unitLabel === "page" ? `p. ${entry.page}` : `Section ${entry.page}`}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
