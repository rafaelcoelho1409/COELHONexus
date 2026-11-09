"use client";

import { Markdown } from "@/shared/markdown";

export function LangGraphTopology({ title, source }: { title: string; source: string | null }) {
  return (
    <section
      aria-label={`${title} compiled LangGraph topology`}
      className="min-w-0 overflow-x-auto rounded-sm border border-border bg-background p-3"
    >
      {source ? (
        <Markdown text={["```mermaid", source, "```"].join("\n")} />
      ) : (
        <p role="status" className="text-xs text-muted-foreground">
          Compiled LangGraph topology is unavailable.
        </p>
      )}
    </section>
  );
}
