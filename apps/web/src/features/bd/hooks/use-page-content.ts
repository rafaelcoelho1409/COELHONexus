"use client";

import { useMemo, useState } from "react";

import { markdownToc } from "@/shared/markdown";
import { diffPages } from "../domain/page-diff";
import type { PageRow } from "../domain/pages";
import type { LivePage } from "../domain/run-state";
import { usePageBody } from "./use-bd-data";

/** Which text of a page is shown: the AI-processed one (what the server serves once a run has the page), the raw Docling
 * one, or what the AI step changed between the two. */
export type PageVersion = "ai" | "docling" | "changes";

/** The text of the page being read and what is derived from it (its outline, the diff). It lives above the reader so
 * the outline column, the reading window and the article all draw from one place. */
export function usePageContent({
  bookId,
  page,
  row,
  livePage,
  agenticRunAvailable,
}: {
  bookId: string;
  page: number | null;
  row: PageRow | undefined;
  livePage: LivePage | undefined;
  agenticRunAvailable: boolean;
}) {
  const [chosenVersion, setVersion] = useState<PageVersion>("ai");
  // A page known only from the manifest, a cut streamed page, or any page once a run exists is read from the server.
  const needsBody =
    row !== undefined &&
    (agenticRunAvailable || livePage === undefined || livePage.truncated || livePage.markdown === "");
  const body = usePageBody(bookId, page, needsBody);
  const hasAi = body.data?.content_source === "agentic";
  const version: PageVersion = hasAi ? chosenVersion : "ai";
  const raw = usePageBody(bookId, page, hasAi && version !== "ai", "docling");

  const aiText = body.data?.markdown ?? livePage?.markdown ?? "";
  const source = version === "docling" ? (raw.data?.markdown ?? "") : aiText;
  const rawText = raw.data?.markdown;
  const diff = useMemo(
    () => (version === "changes" && rawText !== undefined ? diffPages(rawText, aiText) : null),
    [version, rawText, aiText],
  );
  const outline = useMemo(() => (version === "changes" ? [] : markdownToc(source)), [version, source]);
  const outlineIds = useMemo(() => outline.map((entry) => entry.id), [outline]);

  return { body, raw, hasAi, version, setVersion, source, aiText, diff, outline, outlineIds };
}

export type PageContent = ReturnType<typeof usePageContent>;
