"use client";

import { SOURCE_MODES, type SourceMode } from "../params";

/** The Source mode picker — Search | Videos | Playlist | Channel. Same look as the stage tabs. */
export function ModeTabs({ mode, onChange }: { mode: SourceMode; onChange: (mode: SourceMode) => void }) {
  return (
    <nav aria-label="Source mode" className="flex flex-wrap gap-x-1">
      {SOURCE_MODES.map((m) => (
        <button
          key={m.key}
          type="button"
          aria-pressed={m.key === mode}
          onClick={() => onChange(m.key)}
          className={
            m.key === mode
              ? "-mb-px border-b-2 border-primary px-3 py-2 text-sm font-medium text-primary-text"
              : "-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
          }
        >
          {m.label}
        </button>
      ))}
    </nav>
  );
}
