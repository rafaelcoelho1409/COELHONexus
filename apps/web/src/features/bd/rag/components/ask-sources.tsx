"use client";

/** Numbered book sources matching the citation pills in an answer. */

import { BookOpen, ExternalLink } from "lucide-react";
import type { JSX } from "react";
import { useState } from "react";

import { armLabel, citationUrl } from "../ask";
import type { AskCitation } from "../entities";
import { SNIPPET_CAP } from "../params";

function sourceTitle(citation: AskCitation): string {
  if (citation.title) return citation.title;
  if (citation.section) return citation.section;
  return citation.page ? `Page ${citation.page}` : "Book source";
}

function SourceCard({
  citation,
  number,
  active,
}: {
  citation: AskCitation;
  number: number;
  active: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const url = citationUrl(citation);
  const external = /^https?:/i.test(url);
  const snippet = citation.snippet.slice(0, SNIPPET_CAP);
  const arm = armLabel(citation.source);
  const title = sourceTitle(citation);
  return (
    <li
      id={`ask-source-${number}`}
      aria-current={active ? "true" : undefined}
      className={`scroll-mt-32 rounded-sm border p-2.5 ${active ? "border-primary bg-primary/10" : "border-border"}`}
    >
      <div className="flex gap-3">
        <span className="tabular flex size-6 shrink-0 items-center justify-center rounded-sm border border-border text-xs font-medium">
          {number}
        </span>
        <div className="min-w-0 flex-1">
          {url ? (
            <a
              href={url}
              {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              className="block min-h-9 py-1.5 text-sm leading-snug font-medium hover:text-primary-text hover:underline"
            >
              {title}
              {external ? <ExternalLink aria-hidden className="ml-1 inline size-3 align-baseline" /> : null}
              {external ? <span className="sr-only"> (opens in a new tab)</span> : null}
            </a>
          ) : (
            <p className="py-1.5 text-sm leading-snug font-medium">{title}</p>
          )}
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            <BookOpen aria-hidden className="size-3.5" />
            {citation.chapter ? <span className="min-w-0 break-words">{citation.chapter}</span> : null}
            {citation.page ? <span className="tabular">Page {citation.page}</span> : null}
            {arm ? <span className="rounded-sm border border-border px-1.5 py-px">{arm}</span> : null}
          </p>
        </div>
      </div>
      {snippet ? (
        <div className="mt-2 pl-9">
          <p className={`text-xs leading-relaxed text-muted-foreground ${open ? "" : "line-clamp-2"}`}>
            {snippet}
          </p>
          {snippet.length > 110 ? (
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
              className="mt-0.5 min-h-6 text-xs text-primary-text underline underline-offset-2"
            >
              {open ? "Less" : "More"}
            </button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function AskSources({
  citations,
  highlight,
  heading,
}: {
  citations: readonly AskCitation[];
  highlight: number | null;
  heading: string;
}): JSX.Element {
  if (!citations.length) {
    return <p className="text-sm text-muted-foreground">No sources for this answer.</p>;
  }
  return (
    <section aria-label={heading}>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {citations.map((citation, index) => {
          const number = index + 1;
          return (
            <SourceCard key={number} citation={citation} number={number} active={highlight === number} />
          );
        })}
      </ol>
    </section>
  );
}
