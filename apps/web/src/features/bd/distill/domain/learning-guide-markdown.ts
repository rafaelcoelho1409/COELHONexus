import type { LearningGuide } from "../entities";
import { bookPagesHref } from "../../params";
import { diagramMermaidSource } from "./visualization";

type Evidence = { page_number: number; quote: string };
type Lesson = LearningGuide["key_takeaways"][number];

export function learningGuideMarkdown(report: LearningGuide): string {
  const sections = [
    `# ${report.title}`,
    "",
    `- Generated: ${new Date(report.generated_at * 1000).toISOString()}`,
    `- Pages used: ${report.selected_page_count} of ${report.source_page_count}`,
    `- Distill run: \`${report.distill_run_id}\``,
    "",
  ];
  sections.push(
    "## Central thesis",
    "",
    report.central_thesis,
    "",
    evidenceList(report.central_thesis_source_refs, report.book_id),
  );

  if (report.decision_brief) {
    const brief = report.decision_brief;
    sections.push("## Decision brief", "", `**Question:** ${brief.decision_question}`, "");
    sections.push(renderLesson(brief.recommendation, report.book_id));
    for (const option of brief.options) {
      sections.push(
        `### ${option.title}`,
        "",
        option.expected_outcome,
        "",
        `Attribution: ${option.attribution}.`,
        "",
      );
      if (option.tradeoffs.length) sections.push(`Trade-offs: ${option.tradeoffs.join("; ")}`, "");
      if (option.risks.length) sections.push(`Risks: ${option.risks.join("; ")}`, "");
      sections.push(evidenceList(option.source_refs, report.book_id));
    }
    if (brief.assumptions.length) {
      sections.push("### Assumptions", "", ...brief.assumptions.map((item) => `- ${item}`), "");
    }
    if (brief.risks.length) {
      sections.push("### Risks", "", ...brief.risks.map((item) => `- ${item}`), "");
    }
    if (brief.action_plan.length) {
      sections.push(
        "### Action plan",
        "",
        ...brief.action_plan.map(
          (step) =>
            `${step.sequence}. **${step.action}** — ${step.rationale} Success measure: ${step.success_measure}${evidenceInline(step.source_refs, report.book_id)}`,
        ),
        "",
      );
    }
  }

  if (report.research_context.status !== "disabled") {
    sections.push("## Current context and further reading", "");
    for (const finding of report.research_context.findings) {
      sections.push(
        `### ${finding.title}`,
        "",
        finding.summary,
        "",
        `Relation to the book: ${finding.relevance_to_book}`,
        "",
      );
      const sources = finding.source_ids
        .map((id) => report.research_context.sources.find((source) => source.source_id === id))
        .filter((source) => source !== undefined);
      sections.push(...sources.map((source) => `- [${source.title}](${source.url}) — ${source.snippet}`), "");
    }
  }

  sections.push(
    "## Key takeaways",
    "",
    ...report.key_takeaways.map((lesson) => renderLesson(lesson, report.book_id)),
    "",
  );
  sections.push(
    "## Connections across chapters",
    "",
    ...report.cross_chapter_connections.map((lesson) => renderLesson(lesson, report.book_id)),
    "",
  );

  for (const chapter of report.chapters) {
    sections.push(
      `## ${chapter.title}`,
      "",
      chapter.overview,
      "",
      evidenceList(chapter.overview_source_refs, report.book_id),
    );
    if (chapter.lessons.length) {
      sections.push(
        "### Key lessons",
        "",
        ...chapter.lessons.map((lesson) => renderLesson(lesson, report.book_id)),
        "",
      );
    }
    if (chapter.open_questions.length) {
      sections.push(
        "### Open questions",
        "",
        ...chapter.open_questions.map((lesson) => renderLesson(lesson, report.book_id)),
        "",
      );
    }
    sections.push(
      ...diagramSections(chapter.diagrams, report.book_id),
      ...equationSections(chapter.equations, report.book_id),
      ...chartSections(chapter.charts, report.book_id),
      ...timelineSections(chapter.timelines, report.book_id),
      ...mapSections(chapter.maps, report.book_id),
    );
  }

  if (report.glossary.length) {
    sections.push("## Glossary", "");
    for (const item of report.glossary) {
      sections.push(
        `### ${item.term}`,
        "",
        item.definition,
        "",
        evidenceList(item.source_refs, report.book_id),
      );
    }
  }
  if (report.limitation_notes.length) {
    sections.push(
      "## Limitations and caveats",
      "",
      ...report.limitation_notes.map((lesson) => renderLesson(lesson, report.book_id)),
      "",
    );
  }
  if (report.diagrams.length) sections.push(...diagramSections(report.diagrams, report.book_id));
  if (report.equations.length) sections.push(...equationSections(report.equations, report.book_id));
  if (report.charts.length) sections.push(...chartSections(report.charts, report.book_id));
  if (report.maps.length) sections.push(...mapSections(report.maps, report.book_id));
  if (report.review_notes.length) {
    sections.push("## Review notes", "", ...report.review_notes.map((note) => `- ${note}`), "");
  }

  return `${sections
    .filter((section) => section.length > 0)
    .join("\n")
    .trim()}\n`;
}

export function learningGuideMarkdownFilename(report: LearningGuide): string {
  const readableTitle = report.title
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
  return `${readableTitle || "learning-guide"}.md`;
}

function renderLesson(lesson: Lesson, bookId: string): string {
  return `### ${lesson.title}\n\n${lesson.explanation}\n\nAttribution: ${lesson.attribution} · Evidence: ${lesson.verification_status}.\n\n${evidenceList(lesson.source_refs, bookId)}`;
}

function evidenceList(items: readonly Evidence[], bookId: string): string {
  if (items.length === 0) return "";
  return items.map((item) => `- “${item.quote}” — ${pageLink(bookId, item.page_number)}`).join("\n");
}

function evidenceInline(items: readonly Evidence[], bookId: string): string {
  return items.length
    ? ` Sources: ${items.map((item) => pageLink(bookId, item.page_number)).join(", ")}.`
    : "";
}

function pageLink(bookId: string, page: number): string {
  return `[p. ${page}](${bookPagesHref(bookId, page)})`;
}

function diagramSections(diagrams: LearningGuide["diagrams"], bookId: string): string[] {
  return diagrams.map((diagram) => {
    const nodes = diagram.nodes.map((node) => `- **${node.label}**\n${evidenceList(node.evidence, bookId)}`);
    const edges = diagram.edges.map(
      (edge) =>
        `- ${edge.source_id} — ${edge.relation} → ${edge.target_id}\n${evidenceList(edge.evidence, bookId)}`,
    );
    return [
      `### ${diagram.title}`,
      "",
      diagram.accessible_description,
      "",
      "```mermaid",
      diagramMermaidSource(diagram),
      "```",
      "",
      ...nodes,
      ...(edges.length ? ["", ...edges] : []),
      "",
    ].join("\n");
  });
}

function equationSections(equations: LearningGuide["equations"], bookId: string): string[] {
  return equations.map(
    (equation) =>
      `### ${equation.title}\n\n$$\n${equation.latex}\n$$\n\n${equation.explanation}\n\nAttribution: ${equation.attribution}.\n\n${equation.assumptions.map((assumption) => `- Assumption: ${assumption}`).join("\n")}\n\n${evidenceList(equation.evidence, bookId)}`,
  );
}

function chartSections(charts: LearningGuide["charts"], bookId: string): string[] {
  return charts.map((chart) => {
    const columns = chart.dataset.columns.map(
      (column) => `${column.name}${column.unit ? ` (${column.unit})` : ""}`,
    );
    const rows = chart.dataset.rows.map(
      (row) =>
        `| ${chart.dataset.columns.map((column) => escapeTableCell(row.values[column.name] ?? "—")).join(" | ")} | ${escapeTableCell(
          row.evidence.map((item) => `${item.quote} (${pageLink(bookId, item.page_number)})`).join("; ") ||
            "—",
        )} |`,
    );
    return [
      `### ${chart.title}`,
      "",
      chart.caption,
      "",
      chart.accessible_description,
      "",
      `| ${columns.map(escapeTableCell).join(" | ")} | Evidence |`,
      `| ${columns.map(() => "---").join(" | ")} | --- |`,
      ...rows,
      "",
      ...(chart.dataset.transformations.length
        ? [`Transformations: ${chart.dataset.transformations.join("; ")}`, ""]
        : []),
      ...(chart.dataset.assumptions.length
        ? [`Assumptions: ${chart.dataset.assumptions.join("; ")}`, ""]
        : []),
    ].join("\n");
  });
}

function timelineSections(
  timelines: LearningGuide["chapters"][number]["timelines"],
  bookId: string,
): string[] {
  return timelines.map((timeline) =>
    [
      `### ${timeline.title}`,
      "",
      timeline.accessible_description,
      "",
      ...timeline.events.map(
        (event) =>
          `- **${event.date_or_order} · ${event.label}** — ${event.description} (${event.attribution})\n${evidenceList(event.source_refs, bookId)}`,
      ),
      "",
    ].join("\n"),
  );
}

function mapSections(maps: LearningGuide["maps"], bookId: string): string[] {
  return maps.map((map) =>
    [
      `### ${map.title}`,
      "",
      map.accessible_description,
      "",
      "```geojson",
      JSON.stringify(map.geojson, null, 2),
      "```",
      "",
      ...map.geojson.features.map(
        (feature) =>
          `- **${feature.properties.label}** (${feature.properties.attribution})\n${evidenceList(feature.properties.evidence, bookId)}`,
      ),
      "",
    ].join("\n"),
  );
}

function escapeTableCell(value: unknown): string {
  return String(value)
    .replaceAll("|", "\\|")
    .replace(/[\r\n]+/g, " ");
}
