"use client";

/** The sources of one answer: a numbered card per retrieved video, matching the `[n]` pills in the text. Used twice — as the
 * sticky rail beside the conversation on wide screens and inside a sheet on narrow ones. */

import { ExternalLink } from "lucide-react";
import type { JSX } from "react";
import { useState } from "react";

import { armLabel, citationThumb, citationUrl } from "../ask";
import type { AskCitation } from "../entities";
import { SNIPPET_CAP } from "../params";

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
  const thumb = citationThumb(citation);
  const title = citation.title || "Untitled video";
  const snippet = citation.snippet.slice(0, SNIPPET_CAP);
  const arm = armLabel(citation.source);
  return (
    <li
      id={`ask-source-${number}`}
      aria-current={active ? "true" : undefined}
      className={`scroll-mt-32 rounded-sm border p-3 ${active ? "border-primary bg-primary/10" : "border-border"}`}
    >
      <div className="flex gap-3">
        <span className="tabular flex size-6 shrink-0 items-center justify-center rounded-sm border border-border text-xs font-medium">
          {number}
        </span>
        <div className="min-w-0 flex-1">
          <a
            href={url || undefined}
            target="_blank"
            rel="noopener noreferrer"
            className="block py-0.5 text-sm leading-snug font-medium hover:text-primary-text hover:underline"
          >
            {title}
            <ExternalLink aria-hidden className="ml-1 inline size-3 align-baseline" />
            <span className="sr-only"> (opens YouTube in a new tab)</span>
          </a>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            {citation.channel ? <span className="min-w-0 break-words">{citation.channel}</span> : null}
            {arm ? <span className="rounded-sm border border-border px-1.5 py-px">{arm}</span> : null}
          </p>
        </div>
        {/* The same link as the title, so it is hidden from assistive technology and is not a second tab stop. */}
        {/* biome-ignore lint/a11y/useAnchorContent: a duplicate of the title link, deliberately hidden (aria-hidden + tabIndex -1) so it is read and tabbed to once */}
        <a
          href={url || undefined}
          target="_blank"
          rel="noopener noreferrer"
          tabIndex={-1}
          aria-hidden
          className="block aspect-video w-20 shrink-0 self-start overflow-hidden rounded-sm border border-border bg-muted"
        >
          {thumb ? (
            // biome-ignore lint/performance/noImgElement: YouTube thumbnails come from changing CDN hosts
            <img src={thumb} alt="" loading="lazy" className="size-full object-cover" />
          ) : null}
        </a>
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
              onClick={() => setOpen((on) => !on)}
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
  /** The pill number last chosen in the text. */
  highlight: number | null;
  heading: string;
}): JSX.Element {
  if (!citations.length) {
    return <p className="text-sm text-muted-foreground">This answer has no sources.</p>;
  }
  // The answer cites by position, so the number — not the video — is a card's identity.
  const numbered = citations.map((citation, i) => ({ citation, number: i + 1 }));
  return (
    <section aria-label={heading}>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {numbered.map(({ citation, number }) => (
          <SourceCard key={number} citation={citation} number={number} active={highlight === number} />
        ))}
      </ol>
    </section>
  );
}
