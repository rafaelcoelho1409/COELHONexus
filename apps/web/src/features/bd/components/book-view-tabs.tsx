"use client";

import { BOOK_VIEWS, type BookViewId } from "../params";

/** The Agents | Pages choice, drawn like the app's other navbar links. The panels it controls are in `BookView`. */
export function BookViewTabs({ view, onChange }: { view: BookViewId; onChange: (view: BookViewId) => void }) {
  return (
    <div role="tablist" aria-label="Book views" className="flex items-center">
      {BOOK_VIEWS.map(([viewId, label]) => (
        <button
          key={viewId}
          id={`bd-${viewId}-tab`}
          type="button"
          role="tab"
          aria-selected={view === viewId}
          aria-controls={`bd-${viewId}-panel`}
          tabIndex={view === viewId ? 0 : -1}
          onClick={() => onChange(viewId)}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              const nextView = viewId === "pipeline" ? "materials" : "pipeline";
              onChange(nextView);
              document.getElementById(`bd-${nextView}-tab`)?.focus();
            }
          }}
          className={`inline-flex h-9 items-center px-2 text-sm transition-colors sm:px-3 ${
            view === viewId ? "text-foreground" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
