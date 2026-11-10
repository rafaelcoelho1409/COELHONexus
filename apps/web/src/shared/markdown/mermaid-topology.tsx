"use client";

import { Markdown } from "./markdown";

export function MermaidTopology({ title, source, note }: { title: string; source: string; note?: string }) {
  return (
    <section
      aria-label={`${title} topology`}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide diagram can scroll horizontally, so keyboard users must be able to focus it (WCAG 2.1.1)
      tabIndex={0}
      className="min-w-0 overflow-x-auto rounded-sm border border-border bg-background p-3"
    >
      {note ? <p className="mb-3 text-xs text-muted-foreground">{note}</p> : null}
      <Markdown text={["```mermaid", source, "```"].join("\n")} />
    </section>
  );
}
