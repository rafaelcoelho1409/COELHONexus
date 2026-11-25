from __future__ import annotations

import unittest

import domains


class ReportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_ref = domains.bd.distill.ai.schemas.SourceRef(
            source_type = "book",
            book_id = "book",
            extraction_run_id = "extract",
            ingestion_ai_run_id = "ingestion",
            chunk_id = "unit",
            source_key = "bd/books/book/pages/1.md",
            page_number = 1,
            section_path = ["Chapter 1"],
            source_sha256 = "0" * 64,
            quote = "A source-backed statement.",
        )

    def report(self, schema_version: str = "1.4") -> domains.bd.distill.ai.schemas.StudyGuideDocument:
        reference = self.source_ref
        recommendation = domains.bd.distill.ai.schemas.ReportLesson(
            title = "Recommended approach",
            explanation = "Apply the source-backed method.",
            source_refs = [reference],
            attribution = "agent_inference",
            verification_status = "verified",
            confidence = 0.9,
        )
        return domains.bd.distill.ai.schemas.StudyGuideDocument(
            schema_version = schema_version,
            title = "Test Learning Guide",
            book_id = "book",
            extraction_run_id = "extract",
            ingestion_ai_run_id = "ingestion",
            distill_run_id = "distill",
            generated_at = 1.0,
            central_thesis = "A supported thesis.",
            central_thesis_source_refs = [reference],
            key_takeaways = [],
            cross_chapter_connections = [],
            chapters = [
                domains.bd.distill.ai.schemas.ReportSection(
                    title = "Chapter 1",
                    overview = "A concise overview.",
                    overview_source_refs = [reference],
                    lessons = [],
                    timelines = [
                        domains.bd.distill.ai.schemas.ReportTimeline(
                            title = "Process sequence",
                            accessible_description = "Two source-supported steps in order.",
                            events = [
                                domains.bd.distill.ai.schemas.ReportTimelineEvent(
                                    label = "First step",
                                    description = "Establish the baseline.",
                                    date_or_order = "1",
                                    source_refs = [reference],
                                ),
                                domains.bd.distill.ai.schemas.ReportTimelineEvent(
                                    label = "Second step",
                                    description = "Review the result.",
                                    date_or_order = "2",
                                    source_refs = [reference],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
            decision_brief = domains.bd.distill.ai.schemas.DecisionBrief(
                decision_question = "Which approach should be applied?",
                recommendation = recommendation,
                options = [],
                assumptions = ["The cited method applies to this case."],
                risks = [],
                action_plan = [],
            ),
            covered_pages = [1],
            uncovered_pages = [],
            review_required = True,
        )

    def test_report_schema_accepts_new_decision_and_timeline_structures(self) -> None:
        report = self.report()

        self.assertEqual(report.schema_version, "1.4")
        self.assertIsNotNone(report.decision_brief)
        self.assertEqual(len(report.chapters[0].timelines[0].events), 2)

    def test_older_report_schema_remains_readable(self) -> None:
        report = self.report("1.2")

        self.assertEqual(report.schema_version, "1.2")

    def test_renderer_includes_decision_brief_and_timeline(self) -> None:
        html = domains.bd.distill.source.rendering.domain.render_html(self.report())

        self.assertIn("Decision brief", html)
        self.assertIn("Process sequence", html)
        self.assertIn("First step", html)
        self.assertIn("Second step", html)

    def test_report_keeps_external_search_sources_in_a_separate_appendix(self) -> None:
        report = self.report().model_copy(update = {
            "research_context": domains.bd.distill.ai.schemas.ResearchContext(
                status = "complete",
                provider_id = "coelho_search",
                queries = ["current evidence"],
                sources = [
                    domains.bd.distill.ai.schemas.ResearchSource(
                        source_id = "source-1",
                        provider_id = "coelho_search",
                        title = "Current research",
                        url = "https://example.com/research",
                        snippet = "External context summary.",
                        query = "current evidence",
                        accessed_at = 1.0,
                    ),
                ],
            ),
        })

        html = domains.bd.distill.source.rendering.domain.render_html(report)

        self.assertIn("Current context and further reading", html)
        self.assertIn("External references are for current context", html)
        self.assertIn("https://example.com/research", html)


if __name__ == "__main__":
    unittest.main()
