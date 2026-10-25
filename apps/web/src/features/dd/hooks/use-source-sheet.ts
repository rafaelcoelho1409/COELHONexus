"use client";

import { useCallback, useState } from "react";

import { findEntryByBasename } from "../domain/ingestion";
import { useManifest } from "./use-dd-data";

/**
 * The page a chapter cites, opened in a side sheet: the reader clicks a source file name, the manifest is
 * fetched on first use, and the sheet then steps through the whole manifest.
 */
export function useSourceSheet(slug: string) {
  const [name, setName] = useState<string | null>(null);
  const [stepped, setStepped] = useState<number | null>(null);
  const manifest = useManifest(name === null ? null : slug);
  const entries = manifest.data?.entries ?? [];
  const position = stepped ?? (name === null ? -1 : findEntryByBasename(entries, name));

  const open = useCallback((basename: string) => {
    setStepped(null);
    setName(basename);
  }, []);
  const close = useCallback(() => {
    setName(null);
    setStepped(null);
  }, []);

  return {
    entries,
    position,
    isOpen: name !== null && position >= 0,
    /** The manifest loaded and the cited page is not in it (it was wiped or re-ingested). */
    notInLibrary: name !== null && manifest.data !== null && position < 0,
    open,
    close,
    step: setStepped,
  };
}
