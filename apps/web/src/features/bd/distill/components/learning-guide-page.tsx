"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { Markdown } from "@/shared/markdown";
import { readerFont } from "@/shared/markdown/reader-font";
import { bookPagesHref } from "../../params";
import type { LearningGuide, ReportSection } from "../entities";
import { Attribution, CitationList, GuideFact, Lesson } from "./learning-guide-elements";
import type { GuideQuality, StudyPage } from "./learning-guide-reader-domain";
import {
  guideMetrics,
  needsReview,
  pageAnchor,
  pageFacts,
  visualGroups,
} from "./learning-guide-reader-domain";
import { ReportChart as ReportChartView } from "./report-chart";
import { ReportDiagram as ReportDiagramView } from "./report-diagram";
import { ReportMapView } from "./report-map";

export function StudyPageArticle({
  report,
  quality,
  page,
  index,
  total,
  toolbar,
}: {
  report: LearningGuide;
  quality: GuideQuality;
  page: StudyPage;
  index: number;
  total: number;
  toolbar?: ReactNode;
}) {
  const anchor = pageAnchor(page);
  const facts = pageFacts(page, report);
  const reviewRequired = needsReview(page, report);

  return (
    <article
      aria-label={`Study page ${index + 1}: ${page.title}`}
      className="min-w-0 rounded-md border border-border bg-card p-4 sm:p-5"
    >
      <header className="mb-4 border-b border-border pb-4">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="mono-label">
              Study page {index + 1} of {total}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">AI-distilled Learning Guide</p>
            <h2 className="mt-3 w-full break-words text-xl font-semibold tracking-tight">{page.title}</h2>
            <p className="mt-1 w-full break-words text-sm text-muted-foreground">
              Learning Guide › {page.group}
            </p>
          </div>
          {toolbar}
        </div>
        <ul aria-label="Study section details" className="mt-3 flex flex-wrap gap-2">
          {reviewRequired ? <StudyTag tone="warning">Review needed</StudyTag> : null}
          {facts.map((fact) => (
            <StudyTag key={fact}>{fact}</StudyTag>
          ))}
        </ul>
      </header>
      <div className={`${readerFont.className} reader-prose min-w-0`}>
        <StudyPageContent report={report} quality={quality} page={page} anchor={anchor} />
      </div>
    </article>
  );
}

function StudyPageContent({
  report,
  quality,
  page,
  anchor,
}: {
  report: LearningGuide;
  quality: GuideQuality;
  page: StudyPage;
  anchor: string;
}) {
  const pageHref = (number: number) => bookPagesHref(report.book_id, number);

  if (page.kind === "overview") {
    const metrics = guideMetrics(report, quality);
    return (
      <section aria-labelledby={`${anchor}-title`}>
        <h3 id={`${anchor}-title`} className="text-lg font-semibold">
          Guide at a glance
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Generated{" "}
          <time dateTime={new Date(report.generated_at * 1000).toISOString()}>
            {new Date(report.generated_at * 1000).toLocaleString()}
          </time>
          {" · "}Run <span className="break-all font-mono">{report.distill_run_id}</span>
        </p>
        <dl id={`${anchor}-facts`} className="mt-4 grid grid-cols-2 gap-2 xl:grid-cols-4">
          {metrics.map((fact) => (
            <GuideFact key={fact.label} {...fact} />
          ))}
        </dl>
        <aside
          id={`${anchor}-review`}
          aria-label="Learning Guide review status"
          className="mt-5 rounded-sm border border-border bg-background p-3"
        >
          <h4
            className={`font-medium ${report.review_required || report.uncovered_pages.length ? "text-warning-text" : "text-success-text"}`}
          >
            {report.review_required || report.uncovered_pages.length ? "Review needed" : "Coverage complete"}
          </h4>
          <p className="mt-1 text-sm text-muted-foreground">
            {report.covered_pages.length.toLocaleString()} source pages covered ·{" "}
            {report.uncovered_pages.length.toLocaleString()} not covered.
          </p>
          {report.uncovered_pages.length ? (
            <p className="mt-1 break-words text-xs text-muted-foreground">
              Pages to inspect: {report.uncovered_pages.join(", ")}
            </p>
          ) : null}
          {report.review_notes.length ? (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {report.review_notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </aside>
      </section>
    );
  }

  if (page.kind === "thesis") {
    return (
      <section aria-labelledby={`${anchor}-title`}>
        <h3 id={`${anchor}-title`} className="text-lg font-semibold">
          Central thesis
        </h3>
        <Attribution value={report.central_thesis_attribution} />
        <div className="mt-2">
          <Markdown text={report.central_thesis} />
        </div>
        {report.central_thesis_source_refs.length ? (
          <section id={`${anchor}-sources`} className="mt-4 border-t border-border pt-3">
            <h4 className="font-medium">Source references</h4>
            <CitationList sources={report.central_thesis_source_refs} pageHref={pageHref} />
          </section>
        ) : null}
      </section>
    );
  }

  if (page.kind === "takeaways" || page.kind === "connections") {
    const lessons = page.kind === "takeaways" ? report.key_takeaways : report.cross_chapter_connections;
    const title = page.kind === "takeaways" ? "Key takeaways" : "Connections across chapters";
    return (
      <section id={anchor} aria-labelledby={`${anchor}-title`}>
        <h3 id={`${anchor}-title`} className="text-lg font-semibold">
          {title}
        </h3>
        <div className="mt-3 space-y-4">
          {lessons.map((lesson, lessonIndex) => (
            <Lesson
              key={`${lesson.title}-${lesson.source_refs.map((source) => source.chunk_id).join("-")}`}
              id={`${anchor}-lesson-${lessonIndex + 1}`}
              lesson={lesson}
              pageHref={pageHref}
            />
          ))}
        </div>
      </section>
    );
  }

  if (page.kind === "decision-brief") {
    const brief = report.decision_brief;
    if (!brief) return null;
    return (
      <section id={anchor} aria-labelledby={`${anchor}-title`} className="space-y-5">
        <h3 id={`${anchor}-title`} className="text-lg font-semibold">
          Decision brief
        </h3>
        <section>
          <h4 className="font-medium">Decision question</h4>
          <p className="mt-1">{brief.decision_question}</p>
        </section>
        <section>
          <h4 id={`${anchor}-recommendation`} className="font-medium">
            Recommended approach
          </h4>
          <Lesson lesson={brief.recommendation} id={`${anchor}-recommendation-lesson`} pageHref={pageHref} />
        </section>
        {brief.options.length ? (
          <section>
            <h4 className="font-medium">Options</h4>
            <div className="mt-3 space-y-4">
              {brief.options.map((option, optionIndex) => (
                <article
                  id={`${anchor}-option-${optionIndex + 1}`}
                  key={`${option.title}-${optionIndex}`}
                  className="border-t border-border pt-3"
                >
                  <h5 className="font-medium">{option.title}</h5>
                  <p className="mt-1 text-sm">{option.expected_outcome}</p>
                  <Attribution value={option.attribution} />
                  {option.tradeoffs.length ? (
                    <p className="mt-2 text-sm text-muted-foreground">
                      Trade-offs: {option.tradeoffs.join(" · ")}
                    </p>
                  ) : null}
                  {option.risks.length ? (
                    <p className="mt-1 text-sm text-muted-foreground">Risks: {option.risks.join(" · ")}</p>
                  ) : null}
                  <CitationList sources={option.source_refs} pageHref={pageHref} />
                </article>
              ))}
            </div>
          </section>
        ) : null}
        {brief.assumptions.length ? (
          <section>
            <h4 className="font-medium">Assumptions</h4>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {brief.assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </section>
        ) : null}
        {brief.risks.length ? (
          <section>
            <h4 className="font-medium">Risks</h4>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {brief.risks.map((risk) => (
                <li key={risk}>{risk}</li>
              ))}
            </ul>
          </section>
        ) : null}
        {brief.action_plan.length ? (
          <section id={`${anchor}-actions`}>
            <h4 className="font-medium">Action plan</h4>
            <ol className="mt-3 space-y-4">
              {brief.action_plan.map((step) => (
                <li key={step.sequence} className="border-l border-border pl-3">
                  <h5 className="font-medium tabular-nums">
                    {step.sequence}. {step.action}
                  </h5>
                  <p className="mt-1 text-sm">{step.rationale}</p>
                  <Attribution value={step.attribution} />
                  {step.prerequisites.length ? (
                    <p className="mt-1 text-sm text-muted-foreground">
                      Prerequisites: {step.prerequisites.join(" · ")}
                    </p>
                  ) : null}
                  <p className="mt-1 text-sm text-muted-foreground">
                    Success measure: {step.success_measure}
                  </p>
                  <CitationList sources={step.source_refs} pageHref={pageHref} />
                </li>
              ))}
            </ol>
          </section>
        ) : null}
      </section>
    );
  }

  if (page.kind === "research-context") {
    const research = report.research_context;
    return (
      <section id={anchor} aria-labelledby={`${anchor}-title`} className="space-y-5">
        <h3 id={`${anchor}-title`} className="text-lg font-semibold">
          Current context and further reading
        </h3>
        {research.status !== "complete" ? (
          <p className="text-sm text-muted-foreground">
            {research.status === "unavailable"
              ? "External research was unavailable for this run. The book-grounded report remains unchanged."
              : research.status === "no_results"
                ? "No usable external sources were found."
                : "The selected research connection is not supported by this Distill adapter."}
          </p>
        ) : null}
        {research.findings.map((finding, findingIndex) => {
          const sources = finding.source_ids
            .map((sourceId) => research.sources.find((source) => source.source_id === sourceId))
            .filter((source) => source !== undefined);
          return (
            <article
              id={`${anchor}-finding-${findingIndex + 1}`}
              key={`${finding.title}-${findingIndex}`}
              className="border-t border-border pt-3"
            >
              <h4 className="font-medium">{finding.title}</h4>
              <p className="mt-1 text-sm">{finding.summary}</p>
              <p className="mt-2 text-sm text-muted-foreground">
                Relation to this book: {finding.relevance_to_book}
              </p>
              <ul className="mt-3 space-y-3">
                {sources.map((source) => (
                  <li key={source.source_id} className="border-l border-border pl-3">
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="font-medium text-primary-text underline underline-offset-2"
                    >
                      {source.title}
                    </a>
                    <p className="mt-1 text-sm text-muted-foreground">{source.snippet}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {source.publisher ?? source.provider_id}
                      {source.published_at ? ` · Published ${source.published_at}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            </article>
          );
        })}
        {research.sources.length ? (
          <section id={`${anchor}-sources`}>
            <h4 className="font-medium">Retrieved sources</h4>
            <p className="mt-1 text-xs text-muted-foreground">
              External material adds context; it does not count as evidence for claims attributed to the book.
            </p>
            <ul className="mt-3 space-y-3">
              {research.sources.map((source) => (
                <li key={source.source_id} className="border-l border-border pl-3">
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="font-medium text-primary-text underline underline-offset-2"
                  >
                    {source.title}
                  </a>
                  <p className="mt-1 text-sm text-muted-foreground">{source.snippet}</p>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </section>
    );
  }

  if (page.kind === "chapter") {
    const chapter = report.chapters[page.chapterIndex ?? -1];
    if (!chapter) return null;
    return <StudyChapter chapter={chapter} pageHref={pageHref} anchor={anchor} />;
  }

  if (page.kind === "glossary") {
    return (
      <section id={anchor} aria-labelledby={`${anchor}-title`}>
        <h3 id={`${anchor}-title`} className="text-lg font-semibold">
          Glossary
        </h3>
        <dl className="mt-3 space-y-4">
          {report.glossary.map((entry, entryIndex) => (
            <div
              id={`${anchor}-term-${entryIndex + 1}`}
              key={entry.term}
              className="border-t border-border pt-3"
            >
              <dt className="font-medium">{entry.term}</dt>
              <dd className="mt-1 text-sm text-muted-foreground">{entry.definition}</dd>
              <Attribution value={entry.attribution} />
              <CitationList sources={entry.source_refs} pageHref={pageHref} />
              {entry.verification_status === "unverified" ? (
                <p className="mt-1 text-xs text-warning-text">Evidence needs review</p>
              ) : null}
            </div>
          ))}
        </dl>
      </section>
    );
  }

  if (page.kind === "limitations") {
    return (
      <section id={anchor} aria-labelledby={`${anchor}-title`}>
        <h3 id={`${anchor}-title`} className="text-lg font-semibold">
          Limitations and caveats
        </h3>
        <div className="mt-3 space-y-4">
          {report.limitation_notes.map((lesson, lessonIndex) => (
            <Lesson
              key={`${lesson.title}-${lesson.source_refs.map((source) => source.chunk_id).join("-")}`}
              id={`${anchor}-lesson-${lessonIndex + 1}`}
              lesson={lesson}
              pageHref={pageHref}
            />
          ))}
        </div>
      </section>
    );
  }

  return <VisualLibrary report={report} anchor={anchor} pageHref={pageHref} />;
}

function StudyChapter({
  chapter,
  pageHref,
  anchor,
}: {
  chapter: ReportSection;
  pageHref: (page: number) => string;
  anchor: string;
}) {
  const hasVisuals =
    chapter.diagrams.length +
      chapter.equations.length +
      chapter.charts.length +
      chapter.timelines.length +
      chapter.maps.length >
    0;
  return (
    <section id={anchor} aria-labelledby={`${anchor}-title`} className="space-y-5">
      <h3 id={`${anchor}-title`} className="text-lg font-semibold">
        {chapter.title}
      </h3>
      <section id={`${anchor}-overview`}>
        <h4 className="font-medium">Overview</h4>
        <Attribution value={chapter.overview_attribution} />
        <div className="mt-2">
          <Markdown text={chapter.overview} />
        </div>
        <CitationList sources={chapter.overview_source_refs} pageHref={pageHref} />
      </section>
      {chapter.lessons.length ? (
        <section id={`${anchor}-lessons`}>
          <h4 className="font-medium">Key lessons</h4>
          <div className="mt-3 space-y-4">
            {chapter.lessons.map((lesson, lessonIndex) => (
              <Lesson
                key={`${lesson.title}-${lesson.source_refs.map((source) => source.chunk_id).join("-")}`}
                id={`${anchor}-lesson-${lessonIndex + 1}`}
                lesson={lesson}
                pageHref={pageHref}
              />
            ))}
          </div>
        </section>
      ) : null}
      {chapter.open_questions.length ? (
        <section id={`${anchor}-questions`}>
          <h4 className="font-medium">Open questions</h4>
          <div className="mt-3 space-y-4">
            {chapter.open_questions.map((lesson, lessonIndex) => (
              <Lesson
                key={`${lesson.title}-${lesson.source_refs.map((source) => source.chunk_id).join("-")}`}
                id={`${anchor}-question-${lessonIndex + 1}`}
                lesson={lesson}
                pageHref={pageHref}
              />
            ))}
          </div>
        </section>
      ) : null}
      {hasVisuals ? (
        <section id={`${anchor}-visuals`} className="space-y-4">
          <h4 className="font-medium">Visuals and data structures</h4>
          {chapter.diagrams.map((diagram, visualIndex) => (
            <div
              id={`${anchor}-visual-${visualIndex + 1}`}
              key={`diagram-${diagram.title}-${diagram.nodes.map((node) => node.node_id).join("-")}`}
            >
              <ReportDiagramView diagram={diagram} pageHref={pageHref} />
            </div>
          ))}
          {chapter.equations.map((equation) => (
            <EquationView
              key={`equation-${equation.title}-${equation.learning_unit_ids.join("-")}`}
              equation={equation}
              pageHref={pageHref}
            />
          ))}
          {chapter.charts.map((chart, visualIndex) => (
            <div
              id={`${anchor}-chart-${visualIndex + 1}`}
              key={`chart-${chart.title}-${chart.dataset.dataset_id}`}
            >
              <ReportChartView chart={chart} pageHref={pageHref} />
            </div>
          ))}
          {chapter.timelines.map((timeline, visualIndex) => (
            <div id={`${anchor}-timeline-${visualIndex + 1}`} key={`timeline-${timeline.title}`}>
              <TimelineView timeline={timeline} pageHref={pageHref} />
            </div>
          ))}
          {chapter.maps.map((map, visualIndex) => (
            <div id={`${anchor}-map-${visualIndex + 1}`} key={`map-${map.title}`}>
              <ReportMapView map={map} pageHref={pageHref} />
            </div>
          ))}
        </section>
      ) : null}
    </section>
  );
}

function VisualLibrary({
  report,
  anchor,
  pageHref,
}: {
  report: LearningGuide;
  anchor: string;
  pageHref: (page: number) => string;
}) {
  const groups = visualGroups(report);
  return (
    <section id={anchor} aria-labelledby={`${anchor}-title`}>
      <h3 id={`${anchor}-title`} className="text-lg font-semibold">
        Visuals and data structures
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">
        Diagrams, equations, charts, timelines, and maps, grouped by the section they explain.
      </p>
      <div className="mt-4 space-y-6">
        {groups.map((group, groupIndex) => (
          <section
            id={`${anchor}-group-${groupIndex + 1}`}
            key={group.id}
            className="space-y-4 border-t border-border pt-4"
          >
            {groups.length > 1 ? <h4 className="font-medium">{group.title}</h4> : null}
            {group.diagrams.map((diagram, visualIndex) => (
              <div
                id={`${anchor}-diagram-${groupIndex + 1}-${visualIndex + 1}`}
                key={`diagram-${diagram.title}-${diagram.nodes.map((node) => node.node_id).join("-")}`}
              >
                <ReportDiagramView diagram={diagram} pageHref={pageHref} />
              </div>
            ))}
            {group.equations.map((equation, visualIndex) => (
              <div
                id={`${anchor}-equation-${groupIndex + 1}-${visualIndex + 1}`}
                key={`equation-${equation.title}-${equation.learning_unit_ids.join("-")}`}
              >
                <EquationView equation={equation} pageHref={pageHref} />
              </div>
            ))}
            {group.charts.map((chart, visualIndex) => (
              <div
                id={`${anchor}-chart-${groupIndex + 1}-${visualIndex + 1}`}
                key={`chart-${chart.title}-${chart.dataset.dataset_id}`}
              >
                <ReportChartView chart={chart} pageHref={pageHref} />
              </div>
            ))}
            {group.timelines.map((timeline, visualIndex) => (
              <div
                id={`${anchor}-timeline-${groupIndex + 1}-${visualIndex + 1}`}
                key={`timeline-${timeline.title}`}
              >
                <TimelineView timeline={timeline} pageHref={pageHref} />
              </div>
            ))}
            {group.maps.map((map, visualIndex) => (
              <div id={`${anchor}-map-${groupIndex + 1}-${visualIndex + 1}`} key={`map-${map.title}`}>
                <ReportMapView map={map} pageHref={pageHref} />
              </div>
            ))}
          </section>
        ))}
      </div>
    </section>
  );
}

function TimelineView({
  timeline,
  pageHref,
}: {
  timeline: LearningGuide["chapters"][number]["timelines"][number];
  pageHref: (page: number) => string;
}) {
  return (
    <section aria-label={`Timeline: ${timeline.title}`} className="border-t border-border pt-4">
      <h4 className="font-medium">{timeline.title}</h4>
      <p className="mt-1 text-sm text-muted-foreground">{timeline.accessible_description}</p>
      <ol className="mt-3 space-y-4 border-l border-border pl-4">
        {timeline.events.map((event, index) => (
          <li key={`${event.date_or_order}-${event.label}-${index}`} className="relative">
            <h5 className="font-medium">
              <span className="tabular-nums">{event.date_or_order}</span> · {event.label}
            </h5>
            <p className="mt-1 text-sm">{event.description}</p>
            <Attribution value={event.attribution} />
            <CitationList sources={event.source_refs} pageHref={pageHref} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function EquationView({
  equation,
  pageHref,
}: {
  equation: LearningGuide["equations"][number];
  pageHref: (page: number) => string;
}) {
  return (
    <section className="border-t border-border pt-3">
      <h4 className="font-medium">{equation.title}</h4>
      <section
        className="mt-2 overflow-x-auto"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: keyboard users need to focus a horizontally scrollable equation (WCAG 2.1.1)
        tabIndex={0}
        aria-label={`${equation.title} expression`}
      >
        <Markdown text={`$$\n${equation.latex}\n$$`} />
      </section>
      <div className="mt-2">
        <Markdown text={equation.explanation} />
      </div>
      {equation.assumptions.length ? (
        <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
          {equation.assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ul>
      ) : null}
      <Attribution value={equation.attribution} />
      {equation.evidence.length ? (
        <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
          {equation.evidence.map((item) => (
            <li key={`${item.page_number}-${item.quote}`}>
              “{item.quote}” —{" "}
              <Link className="underline underline-offset-2" href={pageHref(item.page_number)}>
                Page {item.page_number}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function StudyTag({ children, tone }: { children: ReactNode; tone?: "warning" }) {
  return (
    <li
      className={`rounded-sm border border-border px-2 py-0.5 text-xs ${tone === "warning" ? "text-warning-text" : "text-muted-foreground"}`}
    >
      {children}
    </li>
  );
}
