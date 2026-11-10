import Link from "next/link";

import { Markdown } from "@/shared/markdown";
import { bookPagesHref } from "../../params";
import type { DistillRun, LearningGuide, ReportLesson, ReportSection, SourceRef } from "../entities";
import { ReportChart } from "./report-chart";
import { ReportDiagram } from "./report-diagram";

type GuideQuality = DistillRun["structural_quality"];

export function LearningGuideView({ report, quality }: { report: LearningGuide; quality: GuideQuality }) {
  const pageHref = (page: number) => bookPagesHref(report.book_id, page);
  const reviewRequired = report.review_required || report.uncovered_pages.length > 0;
  const visualsCount =
    report.diagrams.length +
    report.equations.length +
    report.charts.length +
    report.chapters.reduce(
      (total, chapter) => total + chapter.diagrams.length + chapter.equations.length + chapter.charts.length,
      0,
    );
  const reportPages = new Set([...report.covered_pages, ...report.uncovered_pages]);
  const coveredPages = quality?.covered_substantive_pages ?? report.covered_pages.length;
  const expectedPages = quality?.substantive_page_count ?? reportPages.size;
  const hasSelectionCounts = report.schema_version === "1.1" && report.source_page_count > 0;
  const selectedPages = hasSelectionCounts ? report.selected_page_count : reportPages.size;
  const coverageRate =
    quality?.page_coverage_rate ?? (expectedPages > 0 ? coveredPages / expectedPages : null);
  const contents = [
    { id: "guide-central-thesis", label: "Central thesis" },
    ...(report.key_takeaways.length > 0 ? [{ id: "guide-takeaways", label: "Key takeaways" }] : []),
    ...(report.cross_chapter_connections.length > 0
      ? [{ id: "guide-connections", label: "Chapter connections" }]
      : []),
    ...report.chapters.map((chapter, index) => ({
      id: chapterId(index),
      label: chapter.title,
    })),
    ...(report.glossary.length > 0 ? [{ id: "guide-glossary", label: "Glossary" }] : []),
    ...(report.limitation_notes.length > 0 ? [{ id: "guide-limitations", label: "Limitations" }] : []),
    ...(visualsCount > 0 ? [{ id: "guide-visuals", label: "Figures and models" }] : []),
  ];

  return (
    <article
      aria-labelledby="learning-guide-title"
      className="min-w-0 rounded-md border border-border bg-card p-4 sm:p-6"
    >
      <header className="border-b border-border pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="mono-label">Learning Guide · {report.report_scope.replaceAll("_", " ")}</p>
          <span className="rounded-sm border border-border px-2 py-1 text-xs text-muted-foreground">
            {reviewRequired ? "Review required" : "Review complete"}
          </span>
        </div>
        <h2 id="learning-guide-title" className="mt-2 break-words text-2xl font-semibold tracking-tight">
          {report.title}
        </h2>
        <p className="mt-2 text-xs text-muted-foreground">
          Run <span className="break-all font-mono">{report.distill_run_id}</span> · Generated{" "}
          <time dateTime={new Date(report.generated_at * 1000).toISOString()}>
            {new Date(report.generated_at * 1000).toLocaleString()}
          </time>
        </p>
      </header>

      <section aria-label="Learning Guide at a glance" className="mt-4">
        <dl className="grid grid-cols-2 gap-2 xl:grid-cols-4">
          <GuideFact
            label={quality ? "Included-page coverage" : "Report page coverage"}
            value={coverageRate === null ? "Not reported" : `${Math.round(coverageRate * 100)}%`}
            detail={expectedPages > 0 ? `${coveredPages} of ${expectedPages} included pages` : undefined}
          />
          <GuideFact
            label="Pages used"
            value={selectedPages.toLocaleString()}
            detail={
              hasSelectionCounts
                ? `${report.excluded_page_count} of ${report.source_page_count} left out`
                : undefined
            }
          />
          <GuideFact label="Key takeaways" value={report.key_takeaways.length.toLocaleString()} />
          <GuideFact label="Chapters" value={report.chapters.length.toLocaleString()} />
          <GuideFact label="Figures and models" value={visualsCount.toLocaleString()} />
        </dl>
      </section>

      {contents.length > 1 ? (
        <nav aria-label="Learning Guide contents" className="mt-5 border-y border-border py-3">
          <p className="text-xs font-medium text-muted-foreground">Jump to a section</p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {contents.map((item) => (
              <li key={item.id}>
                <a
                  href={`#${item.id}`}
                  className="inline-flex min-h-9 max-w-64 items-center rounded-sm border border-border px-2.5 text-sm text-foreground underline-offset-2 hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <span className="truncate">{item.label}</span>
                </a>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}

      {reviewRequired ? (
        <aside aria-label="Guide review status" className="mt-4 border-l-2 border-border pl-3">
          <h3 className="text-sm font-semibold text-warning-text">Review needed</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {report.covered_pages.length} included pages covered · {report.uncovered_pages.length} not
            covered.
          </p>
          {report.uncovered_pages.length > 0 ? (
            <p className="mt-1 text-xs text-muted-foreground">
              Pages not covered: {report.uncovered_pages.join(", ")}
            </p>
          ) : null}
          {report.review_notes.length > 0 ? (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {report.review_notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </aside>
      ) : (
        <p className="mt-4 text-sm text-success-text" role="status">
          All included pages are covered.
        </p>
      )}

      <section id="guide-central-thesis" aria-labelledby="guide-thesis-title" className="scroll-mt-24 mt-6">
        <h3 id="guide-thesis-title" className="text-lg font-semibold">
          Central thesis
        </h3>
        <Attribution value={report.central_thesis_attribution} />
        <div className="mt-2">
          <Markdown text={report.central_thesis} />
        </div>
        <CitationList sources={report.central_thesis_source_refs} pageHref={pageHref} />
      </section>

      {report.key_takeaways.length > 0 ? (
        <section id="guide-takeaways" aria-labelledby="guide-takeaways-title" className="scroll-mt-24 mt-6">
          <h3 id="guide-takeaways-title" className="text-lg font-semibold">
            Key takeaways
          </h3>
          <div className="mt-3 space-y-4">
            {report.key_takeaways.map((lesson) => (
              <Lesson key={lessonKey(lesson)} lesson={lesson} pageHref={pageHref} />
            ))}
          </div>
        </section>
      ) : null}

      {report.cross_chapter_connections.length > 0 ? (
        <section
          id="guide-connections"
          aria-labelledby="guide-connections-title"
          className="scroll-mt-24 mt-6"
        >
          <h3 id="guide-connections-title" className="text-lg font-semibold">
            Connections across chapters
          </h3>
          <div className="mt-3 space-y-4">
            {report.cross_chapter_connections.map((lesson) => (
              <Lesson key={lessonKey(lesson)} lesson={lesson} pageHref={pageHref} />
            ))}
          </div>
        </section>
      ) : null}

      <div className="mt-8 space-y-8">
        {report.chapters.map((chapter, index) => (
          <Chapter
            key={`${chapter.title}-${chapter.overview}`}
            id={chapterId(index)}
            chapter={chapter}
            pageHref={pageHref}
          />
        ))}
      </div>

      {report.glossary.length > 0 ? (
        <section id="guide-glossary" aria-labelledby="guide-glossary-title" className="scroll-mt-24 mt-8">
          <h3 id="guide-glossary-title" className="text-lg font-semibold">
            Glossary
          </h3>
          <dl className="mt-3 space-y-4">
            {report.glossary.map((entry) => (
              <div key={entry.term} className="border-t border-border pt-3">
                <dt className="font-medium">{entry.term}</dt>
                <dd className="mt-1 text-sm text-muted-foreground">{entry.definition}</dd>
                <Attribution value={entry.attribution} />
                <CitationList sources={entry.source_refs} pageHref={pageHref} />
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {report.limitation_notes.length > 0 ? (
        <section
          id="guide-limitations"
          aria-labelledby="guide-limitations-title"
          className="scroll-mt-24 mt-8"
        >
          <h3 id="guide-limitations-title" className="text-lg font-semibold">
            Limitations and caveats
          </h3>
          <div className="mt-3 space-y-4">
            {report.limitation_notes.map((lesson) => (
              <Lesson key={lessonKey(lesson)} lesson={lesson} pageHref={pageHref} />
            ))}
          </div>
        </section>
      ) : null}

      {visualsCount > 0 ? (
        <section
          id="guide-visuals"
          aria-labelledby="guide-visuals-title"
          className="scroll-mt-24 mt-8 space-y-5"
        >
          <h3 id="guide-visuals-title" className="text-lg font-semibold">
            Figures and models
          </h3>
          {report.diagrams.map((diagram) => (
            <ReportDiagram key={diagramKey(diagram)} diagram={diagram} pageHref={pageHref} />
          ))}
          {report.equations.map((equation) => (
            <section
              key={equationKey(equation)}
              aria-label={`Equation: ${equation.title}`}
              className="border-t border-border pt-4"
            >
              <h4 className="font-medium">{equation.title}</h4>
              <section
                className="mt-2 overflow-x-auto"
                // biome-ignore lint/a11y/noNoninteractiveTabindex: the equation can scroll horizontally, so keyboard users need to focus it (WCAG 2.1.1)
                tabIndex={0}
                aria-label={`${equation.title} expression`}
              >
                <Markdown text={`$$\n${equation.latex}\n$$`} />
              </section>
              <div className="mt-2">
                <Markdown text={equation.explanation} />
              </div>
              {equation.assumptions.length > 0 ? (
                <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
                  {equation.assumptions.map((assumption) => (
                    <li key={assumption}>{assumption}</li>
                  ))}
                </ul>
              ) : null}
              <Attribution value={equation.attribution} />
              <EvidenceList evidence={equation.evidence} pageHref={pageHref} />
            </section>
          ))}
          {report.charts.map((chart) => (
            <ReportChart key={chartKey(chart)} chart={chart} pageHref={pageHref} />
          ))}
        </section>
      ) : null}
    </article>
  );
}

function GuideFact({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0 rounded-sm border border-border bg-background px-3 py-2.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-words text-lg font-semibold tabular-nums">{value}</dd>
      {detail ? <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p> : null}
    </div>
  );
}

function Chapter({
  chapter,
  id,
  pageHref,
}: {
  chapter: ReportSection;
  id: string;
  pageHref: (page: number) => string;
}) {
  return (
    <section
      id={id}
      aria-label={`Chapter: ${chapter.title}`}
      className="scroll-mt-24 border-t border-border pt-5"
    >
      <h3 className="text-lg font-semibold">{chapter.title}</h3>
      <Attribution value={chapter.overview_attribution} />
      <div className="mt-2">
        <Markdown text={chapter.overview} />
      </div>
      <CitationList sources={chapter.overview_source_refs} pageHref={pageHref} />
      {chapter.lessons.length > 0 ? (
        <div className="mt-4 space-y-4">
          {chapter.lessons.map((lesson) => (
            <Lesson key={lessonKey(lesson)} lesson={lesson} pageHref={pageHref} />
          ))}
        </div>
      ) : null}
      {chapter.open_questions.length > 0 ? (
        <div className="mt-4">
          <h4 className="font-medium">Open questions</h4>
          <div className="mt-2 space-y-4">
            {chapter.open_questions.map((lesson) => (
              <Lesson key={lessonKey(lesson)} lesson={lesson} pageHref={pageHref} />
            ))}
          </div>
        </div>
      ) : null}
      <div className="mt-4 space-y-4">
        {chapter.diagrams.map((diagram) => (
          <ReportDiagram key={diagramKey(diagram)} diagram={diagram} pageHref={pageHref} />
        ))}
        {chapter.equations.map((equation) => (
          <section key={equationKey(equation)} className="border-t border-border pt-3">
            <h4 className="font-medium">{equation.title}</h4>
            <section
              className="mt-2 overflow-x-auto"
              // biome-ignore lint/a11y/noNoninteractiveTabindex: the equation can scroll horizontally, so keyboard users need to focus it (WCAG 2.1.1)
              tabIndex={0}
              aria-label={`${equation.title} expression`}
            >
              <Markdown text={`$$\n${equation.latex}\n$$`} />
            </section>
            <Markdown text={equation.explanation} />
            {equation.assumptions.length > 0 ? (
              <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
                {equation.assumptions.map((assumption) => (
                  <li key={assumption}>{assumption}</li>
                ))}
              </ul>
            ) : null}
            <Attribution value={equation.attribution} />
            <EvidenceList evidence={equation.evidence} pageHref={pageHref} />
          </section>
        ))}
        {chapter.charts.map((chart) => (
          <ReportChart key={chartKey(chart)} chart={chart} pageHref={pageHref} />
        ))}
      </div>
    </section>
  );
}

function Lesson({ lesson, pageHref }: { lesson: ReportLesson; pageHref: (page: number) => string }) {
  return (
    <section className="border-l border-border pl-3">
      <h4 className="font-medium">{lesson.title}</h4>
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

function Attribution({ value }: { value: "book" | "agent_inference" }) {
  return (
    <p className="mt-1 text-xs text-muted-foreground">
      {value === "book" ? "From the book" : "AI inference"}
    </p>
  );
}

function CitationList({
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

function EvidenceList({
  evidence,
  pageHref,
}: {
  evidence: readonly { page_number: number; quote: string }[];
  pageHref: (page: number) => string;
}) {
  if (evidence.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
      {evidence.map((item) => (
        <li key={`${item.page_number}-${item.quote}`}>
          “{item.quote}” —{" "}
          <Link href={pageHref(item.page_number)} className="underline underline-offset-2">
            Page {item.page_number}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function lessonKey(lesson: ReportLesson): string {
  return `${lesson.title}-${lesson.source_refs.map((source) => source.chunk_id).join("-")}`;
}

function diagramKey(diagram: LearningGuide["diagrams"][number]): string {
  return `${diagram.title}-${diagram.nodes.map((node) => node.node_id).join("-")}`;
}

function equationKey(equation: LearningGuide["equations"][number]): string {
  return `${equation.title}-${equation.learning_unit_ids.join("-")}`;
}

function chartKey(chart: LearningGuide["charts"][number]): string {
  return `${chart.title}-${chart.dataset.dataset_id}`;
}

function chapterId(index: number): string {
  return `guide-chapter-${index + 1}`;
}
