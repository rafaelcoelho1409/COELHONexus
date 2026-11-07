import Link from "next/link";

import { Markdown } from "@/shared/markdown";

import { ingestionPageHref } from "../../params";
import type { LearningGuide, ReportLesson, ReportSection, SourceRef } from "../entities";

export function LearningGuideView({ report }: { report: LearningGuide }) {
  return (
    <article
      aria-labelledby="learning-guide-title"
      className="min-w-0 rounded-md border border-border bg-card p-4 sm:p-6"
    >
      <header className="border-b border-border pb-4">
        <p className="mono-label">Learning Guide · {report.report_scope.replaceAll("_", " ")}</p>
        <h2 id="learning-guide-title" className="mt-2 break-words text-xl font-semibold tracking-tight">
          {report.title}
        </h2>
        <p className="mt-2 text-xs text-muted-foreground">
          Run {report.distill_run_id} · Generated {new Date(report.generated_at * 1000).toISOString()}
        </p>
      </header>

      {report.review_required || report.uncovered_pages.length > 0 ? (
        <aside aria-label="Guide review status" className="mt-4 border-l-2 border-border pl-3">
          <h3 className="text-sm font-semibold text-warning-text">Review needed</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {report.covered_pages.length} source pages covered · {report.uncovered_pages.length} not covered.
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
          All source pages are covered.
        </p>
      )}

      <section aria-labelledby="guide-thesis-title" className="mt-6">
        <h3 id="guide-thesis-title" className="text-lg font-semibold">
          Central thesis
        </h3>
        <Attribution value={report.central_thesis_attribution} />
        <div className="mt-2">
          <Markdown text={report.central_thesis} />
        </div>
        <CitationList bookId={report.book_id} sources={report.central_thesis_source_refs} />
      </section>

      <section aria-labelledby="guide-takeaways-title" className="mt-6">
        <h3 id="guide-takeaways-title" className="text-lg font-semibold">
          Key takeaways
        </h3>
        <div className="mt-3 space-y-4">
          {report.key_takeaways.map((lesson) => (
            <Lesson key={lessonKey(lesson)} bookId={report.book_id} lesson={lesson} />
          ))}
        </div>
      </section>

      {report.cross_chapter_connections.length > 0 ? (
        <section aria-labelledby="guide-connections-title" className="mt-6">
          <h3 id="guide-connections-title" className="text-lg font-semibold">
            Connections across chapters
          </h3>
          <div className="mt-3 space-y-4">
            {report.cross_chapter_connections.map((lesson) => (
              <Lesson key={lessonKey(lesson)} bookId={report.book_id} lesson={lesson} />
            ))}
          </div>
        </section>
      ) : null}

      <div className="mt-8 space-y-8">
        {report.chapters.map((chapter) => (
          <Chapter key={`${chapter.title}-${chapter.overview}`} bookId={report.book_id} chapter={chapter} />
        ))}
      </div>

      {report.glossary.length > 0 ? (
        <section aria-labelledby="guide-glossary-title" className="mt-8">
          <h3 id="guide-glossary-title" className="text-lg font-semibold">
            Glossary
          </h3>
          <dl className="mt-3 space-y-4">
            {report.glossary.map((entry) => (
              <div key={entry.term} className="border-t border-border pt-3">
                <dt className="font-medium">{entry.term}</dt>
                <dd className="mt-1 text-sm text-muted-foreground">{entry.definition}</dd>
                <Attribution value={entry.attribution} />
                <CitationList bookId={report.book_id} sources={entry.source_refs} />
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {report.limitation_notes.length > 0 ? (
        <section aria-labelledby="guide-limitations-title" className="mt-8">
          <h3 id="guide-limitations-title" className="text-lg font-semibold">
            Limitations and caveats
          </h3>
          <div className="mt-3 space-y-4">
            {report.limitation_notes.map((lesson) => (
              <Lesson key={lessonKey(lesson)} bookId={report.book_id} lesson={lesson} />
            ))}
          </div>
        </section>
      ) : null}

      <section aria-labelledby="guide-visuals-title" className="mt-8 space-y-5">
        <h3 id="guide-visuals-title" className="text-lg font-semibold">
          Figures and models
        </h3>
        {report.diagrams.map((diagram) => (
          <section
            key={diagramKey(diagram)}
            aria-label={`Diagram: ${diagram.title}`}
            className="border-t border-border pt-4"
          >
            <h4 className="font-medium">{diagram.title}</h4>
            <p className="mt-1 text-sm text-muted-foreground">{diagram.accessible_description}</p>
            <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm">
              {diagram.nodes.map((node) => (
                <li key={node.node_id}>
                  <span className="font-medium">{node.label}</span>
                  <Attribution value={node.attribution} />
                  <EvidenceList bookId={report.book_id} evidence={node.evidence} />
                </li>
              ))}
            </ol>
            <ul
              aria-label={`${diagram.title} relationships`}
              className="mt-3 space-y-2 border-l border-border pl-4 text-sm text-muted-foreground"
            >
              {diagram.edges.map((edge) => {
                const source = diagram.nodes.find((node) => node.node_id === edge.source_id);
                const target = diagram.nodes.find((node) => node.node_id === edge.target_id);
                return (
                  <li
                    key={`${edge.source_id}-${edge.target_id}-${edge.relation}-${edge.learning_unit_ids.join("-")}`}
                  >
                    {source?.label ?? edge.source_id} — {edge.relation.replaceAll("_", " ")} →{" "}
                    {target?.label ?? edge.target_id}
                    <EvidenceList bookId={report.book_id} evidence={edge.evidence} />
                  </li>
                );
              })}
            </ul>
          </section>
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
            <EvidenceList bookId={report.book_id} evidence={equation.evidence} />
          </section>
        ))}
        {report.charts.map((chart) => (
          <section
            key={chartKey(chart)}
            aria-label={`Chart data: ${chart.title}`}
            className="border-t border-border pt-4"
          >
            <h4 className="font-medium">{chart.title}</h4>
            <p className="mt-1 text-sm text-muted-foreground">{chart.accessible_description}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {chart.caption} · {chart.chart_type} data
            </p>
            <div className="mt-3 space-y-3">
              {chart.dataset.rows.map((row) => (
                <dl
                  key={JSON.stringify(row.values)}
                  className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border pt-2 sm:grid-cols-2"
                >
                  {chart.dataset.columns.map((column) => (
                    <div key={column.name} className="min-w-0">
                      <dt className="text-xs text-muted-foreground">
                        {column.name}
                        {column.unit ? ` · ${column.unit}` : ""}
                      </dt>
                      <dd className="break-words text-sm">{String(row.values[column.name] ?? "—")}</dd>
                    </div>
                  ))}
                  <div className="sm:col-span-2">
                    <EvidenceList bookId={report.book_id} evidence={row.evidence} />
                  </div>
                </dl>
              ))}
            </div>
            {chart.dataset.transformations.length > 0 ? (
              <p className="mt-3 text-xs text-muted-foreground">
                Transformations: {chart.dataset.transformations.join(" · ")}
              </p>
            ) : null}
            {chart.dataset.assumptions.length > 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">
                Assumptions: {chart.dataset.assumptions.join(" · ")}
              </p>
            ) : null}
          </section>
        ))}
      </section>
    </article>
  );
}

function Chapter({ bookId, chapter }: { bookId: string; chapter: ReportSection }) {
  return (
    <section aria-label={`Chapter: ${chapter.title}`} className="border-t border-border pt-5">
      <h3 className="text-lg font-semibold">{chapter.title}</h3>
      <Attribution value={chapter.overview_attribution} />
      <div className="mt-2">
        <Markdown text={chapter.overview} />
      </div>
      <CitationList bookId={bookId} sources={chapter.overview_source_refs} />
      {chapter.lessons.length > 0 ? (
        <div className="mt-4 space-y-4">
          {chapter.lessons.map((lesson) => (
            <Lesson key={lessonKey(lesson)} bookId={bookId} lesson={lesson} />
          ))}
        </div>
      ) : null}
      {chapter.open_questions.length > 0 ? (
        <div className="mt-4">
          <h4 className="font-medium">Open questions</h4>
          <div className="mt-2 space-y-4">
            {chapter.open_questions.map((lesson) => (
              <Lesson key={lessonKey(lesson)} bookId={bookId} lesson={lesson} />
            ))}
          </div>
        </div>
      ) : null}
      <div className="mt-4 space-y-4">
        {chapter.diagrams.map((diagram) => (
          <section
            key={diagramKey(diagram)}
            aria-label={`Diagram: ${diagram.title}`}
            className="border-t border-border pt-3"
          >
            <h4 className="font-medium">{diagram.title}</h4>
            <p className="mt-1 text-sm text-muted-foreground">{diagram.accessible_description}</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {diagram.nodes.map((node) => (
                <li key={node.node_id}>
                  <span className="font-medium">{node.label}</span>
                  <Attribution value={node.attribution} />
                  <EvidenceList bookId={bookId} evidence={node.evidence} />
                </li>
              ))}
            </ul>
            <ul
              aria-label={`${diagram.title} relationships`}
              className="mt-2 space-y-1 border-l border-border pl-4 text-sm text-muted-foreground"
            >
              {diagram.edges.map((edge) => {
                const source = diagram.nodes.find((node) => node.node_id === edge.source_id);
                const target = diagram.nodes.find((node) => node.node_id === edge.target_id);
                return (
                  <li
                    key={`${edge.source_id}-${edge.target_id}-${edge.relation}-${edge.learning_unit_ids.join("-")}`}
                  >
                    {source?.label ?? edge.source_id} — {edge.relation.replaceAll("_", " ")} →{" "}
                    {target?.label ?? edge.target_id}
                    <EvidenceList bookId={bookId} evidence={edge.evidence} />
                  </li>
                );
              })}
            </ul>
          </section>
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
            <EvidenceList bookId={bookId} evidence={equation.evidence} />
          </section>
        ))}
        {chapter.charts.map((chart) => (
          <section key={chartKey(chart)} className="border-t border-border pt-3">
            <h4 className="font-medium">{chart.title}</h4>
            <p className="mt-1 text-sm text-muted-foreground">{chart.accessible_description}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {chart.caption} · {chart.chart_type} data
            </p>
            <ul className="mt-2 space-y-2 text-sm">
              {chart.dataset.rows.map((row) => (
                <li key={JSON.stringify(row.values)} className="border-t border-border pt-2">
                  {chart.dataset.columns
                    .map((column) => `${column.name}: ${String(row.values[column.name] ?? "—")}`)
                    .join(" · ")}
                  <EvidenceList bookId={bookId} evidence={row.evidence} />
                </li>
              ))}
            </ul>
            {chart.dataset.transformations.length > 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                Transformations: {chart.dataset.transformations.join(" · ")}
              </p>
            ) : null}
            {chart.dataset.assumptions.length > 0 ? (
              <p className="mt-1 text-xs text-muted-foreground">
                Assumptions: {chart.dataset.assumptions.join(" · ")}
              </p>
            ) : null}
          </section>
        ))}
      </div>
    </section>
  );
}

function Lesson({ bookId, lesson }: { bookId: string; lesson: ReportLesson }) {
  return (
    <section className="border-l border-border pl-3">
      <h4 className="font-medium">{lesson.title}</h4>
      <Attribution value={lesson.attribution} />
      <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">{lesson.explanation}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {lesson.verification_status === "verified" ? "Evidence checked" : "Evidence needs review"} ·
        confidence {Math.round(lesson.confidence * 100)}%
      </p>
      <CitationList bookId={bookId} sources={lesson.source_refs} />
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

function CitationList({ bookId, sources }: { bookId: string; sources: readonly SourceRef[] }) {
  if (sources.length === 0) return null;
  const pages = [...new Set(sources.map((source) => source.page_number))].sort((left, right) => left - right);
  return (
    <p className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-xs">
      <span className="text-muted-foreground">Sources:</span>
      {pages.map((page) => (
        <Link
          key={page}
          href={ingestionPageHref(bookId, page)}
          className="inline-flex min-h-9 items-center text-primary-text underline underline-offset-2"
        >
          Page {page}
        </Link>
      ))}
    </p>
  );
}

function EvidenceList({
  bookId,
  evidence,
}: {
  bookId: string;
  evidence: readonly { page_number: number; quote: string }[];
}) {
  if (evidence.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
      {evidence.map((item) => (
        <li key={`${item.page_number}-${item.quote}`}>
          “{item.quote}” —{" "}
          <Link href={ingestionPageHref(bookId, item.page_number)} className="underline underline-offset-2">
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
