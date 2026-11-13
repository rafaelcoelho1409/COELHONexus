import type { TocEntry } from "@/shared/markdown";
import type { DistillRun, LearningGuide, ReportChart, ReportDiagram } from "../entities";

export type StudyPage = {
  id: string;
  title: string;
  group: string;
  kind:
    | "overview"
    | "thesis"
    | "takeaways"
    | "connections"
    | "chapter"
    | "glossary"
    | "limitations"
    | "visuals";
  chapterIndex?: number;
};

export type GuideQuality = DistillRun["structural_quality"];
type VisualGroup = {
  id: string;
  title: string;
  diagrams: readonly ReportDiagram[];
  equations: readonly LearningGuide["equations"][number][];
  charts: readonly ReportChart[];
};
export function createStudyPages(report: LearningGuide): StudyPage[] {
  const pages: StudyPage[] = [
    { id: "guide-overview", title: "Guide overview", group: "Guide", kind: "overview" },
    { id: "central-thesis", title: "Central thesis", group: "Core lessons", kind: "thesis" },
  ];
  if (report.key_takeaways.length)
    pages.push({ id: "key-takeaways", title: "Key takeaways", group: "Core lessons", kind: "takeaways" });
  if (report.cross_chapter_connections.length)
    pages.push({
      id: "chapter-connections",
      title: "Chapter connections",
      group: "Core lessons",
      kind: "connections",
    });
  report.chapters.forEach((chapter, chapterIndex) => {
    pages.push({
      id: `chapter-${chapterIndex + 1}`,
      title: chapter.title,
      group: "Chapters",
      kind: "chapter",
      chapterIndex,
    });
  });
  if (report.glossary.length)
    pages.push({ id: "glossary", title: "Glossary", group: "Reference", kind: "glossary" });
  if (report.limitation_notes.length)
    pages.push({
      id: "limitations",
      title: "Limitations and caveats",
      group: "Reference",
      kind: "limitations",
    });
  if (visualGroups(report).length)
    pages.push({ id: "visual-library", title: "Figures and models", group: "Visuals", kind: "visuals" });
  return pages;
}

export function pageAnchor(page: StudyPage): string {
  return `study-${page.id}-content`;
}

export function pageOutline(page: StudyPage, report: LearningGuide): TocEntry[] {
  const anchor = pageAnchor(page);
  const entry = (id: string, text: string): TocEntry => ({ id, text, level: 3 });
  if (page.kind === "overview")
    return [entry(`${anchor}-title`, "Guide at a glance"), entry(`${anchor}-review`, "Coverage and review")];
  if (page.kind === "thesis") {
    return [
      entry(`${anchor}-title`, "Central thesis"),
      ...(report.central_thesis_source_refs.length ? [entry(`${anchor}-sources`, "Source references")] : []),
    ];
  }
  if (page.kind === "takeaways" || page.kind === "connections") {
    const lessons = page.kind === "takeaways" ? report.key_takeaways : report.cross_chapter_connections;
    return [
      entry(`${anchor}-title`, page.title),
      ...lessons.map((lesson, index) => entry(`${anchor}-lesson-${index + 1}`, lesson.title)),
    ];
  }
  if (page.kind === "chapter") {
    const chapter = report.chapters[page.chapterIndex ?? -1];
    if (!chapter) return [];
    return [
      entry(`${anchor}-title`, chapter.title),
      entry(`${anchor}-overview`, "Overview"),
      ...(chapter.lessons.length ? [entry(`${anchor}-lessons`, "Key lessons")] : []),
      ...(chapter.open_questions.length ? [entry(`${anchor}-questions`, "Open questions")] : []),
      ...(chapter.diagrams.length + chapter.equations.length + chapter.charts.length
        ? [entry(`${anchor}-visuals`, "Figures and models")]
        : []),
    ];
  }
  if (page.kind === "glossary") {
    return [
      entry(`${anchor}-title`, "Glossary"),
      ...report.glossary.map((term, index) => entry(`${anchor}-term-${index + 1}`, term.term)),
    ];
  }
  if (page.kind === "limitations") {
    return [
      entry(`${anchor}-title`, "Limitations and caveats"),
      ...report.limitation_notes.map((lesson, index) => entry(`${anchor}-lesson-${index + 1}`, lesson.title)),
    ];
  }
  const groups = visualGroups(report);
  return [
    entry(`${anchor}-title`, "Figures and models"),
    ...groups.map((group, groupIndex) => entry(`${anchor}-group-${groupIndex + 1}`, group.title)),
  ];
}

export function pageFacts(page: StudyPage, report: LearningGuide): string[] {
  if (page.kind === "overview")
    return [
      `${report.chapters.length} distilled sections`,
      `${report.selected_page_count.toLocaleString()} source pages used`,
    ];
  if (page.kind === "thesis")
    return [
      `${new Set(report.central_thesis_source_refs.map((source) => source.page_number)).size} cited pages`,
    ];
  if (page.kind === "takeaways") {
    const pending = reviewCount(report.key_takeaways);
    return [`${report.key_takeaways.length} takeaways`, ...(pending ? [`${pending} need review`] : [])];
  }
  if (page.kind === "connections") {
    const pending = reviewCount(report.cross_chapter_connections);
    return [
      `${report.cross_chapter_connections.length} connections`,
      ...(pending ? [`${pending} need review`] : []),
    ];
  }
  if (page.kind === "chapter") {
    const chapter = report.chapters[page.chapterIndex ?? -1];
    if (!chapter) return [];
    const visuals = chapter.diagrams.length + chapter.equations.length + chapter.charts.length;
    const sources = new Set([
      ...chapter.overview_source_refs.map((source) => source.page_number),
      ...chapter.lessons.flatMap((lesson) => lesson.source_refs.map((source) => source.page_number)),
    ]).size;
    return [
      `${chapter.lessons.length} lessons`,
      `${sources} cited pages`,
      ...(visuals ? [`${visuals} figures and models`] : []),
    ];
  }
  if (page.kind === "glossary") {
    const pending = report.glossary.filter((term) => term.verification_status === "unverified").length;
    return [`${report.glossary.length} terms`, ...(pending ? [`${pending} need review`] : [])];
  }
  if (page.kind === "limitations") return [`${report.limitation_notes.length} caveats`];
  return [
    `${visualGroups(report).reduce((total, group) => total + group.diagrams.length + group.equations.length + group.charts.length, 0)} figures and models`,
  ];
}

export function needsReview(page: StudyPage, report: LearningGuide | undefined): boolean {
  if (!report) return false;
  if (page.kind === "overview") return report.review_required || report.uncovered_pages.length > 0;
  if (page.kind === "thesis") return report.central_thesis_attribution === "agent_inference";
  if (page.kind === "takeaways")
    return report.key_takeaways.some((lesson) => lesson.verification_status === "unverified");
  if (page.kind === "connections")
    return report.cross_chapter_connections.some((lesson) => lesson.verification_status === "unverified");
  if (page.kind === "chapter") {
    const chapter = report.chapters[page.chapterIndex ?? -1];
    return (
      chapter !== undefined &&
      (chapter.open_questions.length > 0 ||
        chapter.lessons.some((lesson) => lesson.verification_status === "unverified"))
    );
  }
  if (page.kind === "glossary")
    return report.glossary.some((term) => term.verification_status === "unverified");
  if (page.kind === "limitations")
    return report.limitation_notes.some((lesson) => lesson.verification_status === "unverified");
  return false;
}

function reviewCount(lessons: readonly { verification_status: "verified" | "unverified" }[]): number {
  return lessons.filter((lesson) => lesson.verification_status === "unverified").length;
}

export function guideMetrics(report: LearningGuide, quality: GuideQuality) {
  const reportPages = new Set([...report.covered_pages, ...report.uncovered_pages]);
  const coveredPages = quality?.covered_substantive_pages ?? report.covered_pages.length;
  const expectedPages = quality?.substantive_page_count ?? reportPages.size;
  const selectedPages =
    report.schema_version === "1.1" && report.source_page_count > 0
      ? report.selected_page_count
      : reportPages.size;
  const visuals = visualGroups(report).reduce(
    (total, group) => total + group.diagrams.length + group.equations.length + group.charts.length,
    0,
  );
  const coverageRate =
    quality?.page_coverage_rate ?? (expectedPages > 0 ? coveredPages / expectedPages : null);
  return [
    {
      label: quality ? "Included-page coverage" : "Report page coverage",
      value: coverageRate === null ? "Not reported" : `${Math.round(coverageRate * 100)}%`,
      detail: expectedPages > 0 ? `${coveredPages} of ${expectedPages} included pages` : undefined,
    },
    {
      label: "Pages used",
      value: selectedPages.toLocaleString(),
      detail:
        report.schema_version === "1.1" ? `${report.excluded_page_count} excluded from Distill` : undefined,
    },
    { label: "Key takeaways", value: report.key_takeaways.length.toLocaleString() },
    { label: "Distilled sections", value: report.chapters.length.toLocaleString() },
    { label: "Figures and models", value: visuals.toLocaleString() },
  ];
}

export function visualGroups(report: LearningGuide): VisualGroup[] {
  const groups: VisualGroup[] = [];
  if (report.diagrams.length || report.equations.length || report.charts.length) {
    groups.push({
      id: "guide-visuals",
      title: "Learning Guide overview",
      diagrams: report.diagrams,
      equations: report.equations,
      charts: report.charts,
    });
  }
  report.chapters.forEach((chapter, index) => {
    if (chapter.diagrams.length || chapter.equations.length || chapter.charts.length) {
      groups.push({
        id: `chapter-${index + 1}`,
        title: chapter.title,
        diagrams: chapter.diagrams,
        equations: chapter.equations,
        charts: chapter.charts,
      });
    }
  });
  return groups;
}
