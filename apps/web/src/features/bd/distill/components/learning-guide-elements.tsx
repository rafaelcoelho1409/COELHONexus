import Link from "next/link";

import type { ReportLesson, SourceRef } from "../entities";

export function GuideFact({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0 rounded-sm border border-border bg-background px-3 py-2.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-words text-lg font-semibold tabular-nums">{value}</dd>
      {detail ? <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p> : null}
    </div>
  );
}

export function Lesson({
  lesson,
  pageHref,
  id,
}: {
  lesson: ReportLesson;
  pageHref: (page: number) => string;
  id?: string;
}) {
  return (
    <section className="border-l border-border pl-3">
      <h4 id={id} className="font-medium">
        {lesson.title}
      </h4>
      <Attribution value={lesson.attribution} />
      <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">{lesson.explanation}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {lesson.verification_status === "verified" ? "Evidence checked" : "Evidence needs review"} ·
        confidence {Math.round(lesson.confidence * 100)}%
      </p>
      <CitationList sources={lesson.source_refs} pageHref={pageHref} />
    </section>
  );
}

export function Attribution({ value }: { value: "book" | "agent_inference" }) {
  return (
    <p className="mt-1 text-xs text-muted-foreground">
      {value === "book" ? "From the book" : "AI inference"}
    </p>
  );
}

export function CitationList({
  sources,
  pageHref,
}: {
  sources: readonly SourceRef[];
  pageHref: (page: number) => string;
}) {
  if (sources.length === 0) return null;
  const pages = [...new Set(sources.map((source) => source.page_number))].sort((left, right) => left - right);
  return (
    <p className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-xs">
      <span className="text-muted-foreground">Sources:</span>
      {pages.map((page) => (
        <Link
          key={page}
          href={pageHref(page)}
          className="inline-flex min-h-9 items-center text-primary-text underline underline-offset-2"
        >
          Page {page}
        </Link>
      ))}
    </p>
  );
}
