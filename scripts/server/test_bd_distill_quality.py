"""Offline contracts for Learning Guide quality checks and report rendering.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_distill_quality.py
"""
import asyncio
import os
import sys
from pathlib import Path

for name, value in {
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "REDIS_PASSWORD": "x",
    "ENVIRONMENT": "local",
}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains  # noqa: E402
import infra  # noqa: E402


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def source_ref(quote: str = "A source-backed statement."):
    return domains.bd.distill.schemas.SourceRef(
        source_type = "book",
        book_id = "book1",
        extraction_run_id = "extract1",
        ingestion_ai_run_id = "qa1",
        chunk_id = "chunk1",
        source_key = "normalized/page-1.md",
        page_number = 1,
        section_path = ["Chapter 1"],
        source_sha256 = domains.bd.distill.domain.sha256_text("A source-backed statement. More text."),
        quote = quote,
    )


def build_report():
    schemas = domains.bd.distill.schemas
    evidence = schemas.EvidenceQuote(page_number = 1, quote = "A source-backed statement.")
    unit = source_ref()
    lesson = schemas.ReportLesson(
        title = "Core idea",
        explanation = "A source-backed statement.",
        source_refs = [unit],
        verification_status = "verified",
        confidence = 0.95,
    )
    diagram = schemas.DiagramSpec(
        title = "A sourced relationship",
        accessible_description = "The first idea supports the second idea.",
        nodes = [
            schemas.DiagramNode(
                node_id = "idea1", label = "First idea", learning_unit_ids = ["unit1"], evidence = [evidence],
            ),
            schemas.DiagramNode(
                node_id = "idea2", label = "Second idea", learning_unit_ids = ["unit1"], evidence = [evidence],
            ),
        ],
        edges = [schemas.DiagramEdge(
            source_id = "idea1", target_id = "idea2", relation = "supports",
            learning_unit_ids = ["unit1"], evidence = [evidence],
        )],
    )
    equation = schemas.EquationBlock(
        title = "Example equation",
        latex = "x = y",
        explanation = "The example is shown for rendering-contract coverage.",
        learning_unit_ids = ["unit1"],
        evidence = [evidence],
    )
    return schemas.StudyGuideDocument(
        title = "Guide <script>alert(1)</script>",
        book_id = "book1",
        extraction_run_id = "extract1",
        ingestion_ai_run_id = "qa1",
        distill_run_id = "run1",
        generated_at = 1.0,
        central_thesis = "A source-backed statement.",
        central_thesis_source_refs = [unit],
        key_takeaways = [lesson],
        cross_chapter_connections = [],
        chapters = [schemas.ReportSection(
            title = "Chapter 1",
            overview = "A source-backed statement.",
            overview_source_refs = [unit],
            lessons = [lesson],
        )],
        diagrams = [diagram],
        equations = [equation],
        covered_pages = [1],
        uncovered_pages = [2],
        review_required = True,
    )


report = build_report()
rendered = domains.bd.distill.domain.render_html(report)
check("whole-book diagrams are part of the validated report contract and rendered output",
      "Whole-book diagrams" in rendered and "A sourced relationship" in rendered and "<svg" in rendered, True)
check("whole-book equations are rendered from the validated report model",
      "Whole-book equations" in rendered and "x = y" in rendered, True)
check("the HTML report includes chapter navigation and a working sibling PDF link",
      ("Learning Guide contents" in rendered, 'href="report.pdf"' in rendered, 'href="#chapter-1"' in rendered),
      (True, True, True))
check("book content is escaped before HTML rendering", "<script>alert(1)</script>" in rendered, False)

page_text = "A source-backed statement. More text."
source_pages = [
    {
        "page_number": 1,
        "source_key": "normalized/page-1.md",
        "source_sha256": domains.bd.distill.domain.sha256_text(page_text),
        "disposition": "deliver",
    },
    {
        "page_number": 2,
        "source_key": "normalized/page-2.md",
        "source_sha256": domains.bd.distill.domain.sha256_text(""),
        "disposition": "discarded_blank",
    },
]
quality = domains.bd.distill.evaluation.assess_report_structure(
    report,
    expected_page_count = 2,
    source_pages = source_pages,
    source_text_by_page = {1: page_text},
)
check("coverage excludes pages explicitly classified as blank", quality.page_coverage_rate, 1.0)
check("all pinned page references and evidence quotes resolve", (
    quality.citation_integrity_passed, quality.quote_support_passed, quality.errors,
), (True, True, []))

missing_text_quality = domains.bd.distill.evaluation.assess_report_structure(
    report,
    expected_page_count = 2,
    source_pages = source_pages,
    source_text_by_page = {},
)
check("citation checks fail closed when the source page is unavailable", missing_text_quality.quote_support_passed, False)

review_type = domains.bd.distill.evaluation.HumanLearningReview
reviews = [
    review_type(
        book_id = f"book{number}",
        distill_run_id = f"run{number}",
        coverage_rating = 5,
        citation_accuracy_rating = 5,
        clarity_rating = 4,
        usefulness_rating = 4,
        source_comprehension_score = 80,
        guide_comprehension_score = 82,
        source_delayed_recall_score = 70,
        guide_delayed_recall_score = 72,
        guide_transfer_score = 75,
        source_reading_minutes = 300,
        guide_reading_minutes = 90,
    )
    for number in range(1, 4)
]
aggregate = domains.bd.distill.evaluation.aggregate_human_reviews(reviews)
check("three positive paired human reviews meet the initial small-beta gate",
      (aggregate["reviewed_book_count"], aggregate["small_beta_gate_passed"]), (3, True))
check("beta evaluation reports each book separately", sorted(aggregate["per_book"]), ["book1", "book2", "book3"])
reviews[0] = reviews[0].model_copy(update = {"unsupported_claims": ["lesson-1"]})
aggregate = domains.bd.distill.evaluation.aggregate_human_reviews(reviews)
check("a critical unsupported-claim review blocks the small-beta gate",
      (aggregate["unsupported_claim_count"], aggregate["small_beta_gate_passed"]), (1, False))
reviews[0] = reviews[0].model_copy(update = {"unsupported_claims": []})
reviews[0] = reviews[0].model_copy(update = {"guide_comprehension_score": 60})
aggregate = domains.bd.distill.evaluation.aggregate_human_reviews(reviews)
check("a weak learning result for one book cannot be hidden by the pooled average",
      (aggregate["per_book"]["book1"]["comprehension_delta"], aggregate["small_beta_gate_passed"]), (-20.0, False))

print("Distill quality checks passed")


class FakePage:
    async def route(self, pattern, handler) -> None:
        self.route_pattern = pattern
        self.route_handler = handler

    async def set_content(self, content, *, wait_until, timeout) -> None:
        self.content = content
        self.wait_until = wait_until
        self.timeout = timeout

    async def pdf(self, **options) -> bytes:
        self.pdf_options = options
        return b"%PDF-1.7\nmocked PDF artifact"


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()

    async def new_page(self):
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext()

    async def new_context(self, **options):
        self.context_options = options
        return self.context


class FakeChromium:
    def __init__(self, browser) -> None:
        self.browser = browser

    async def connect_over_cdp(self, endpoint, *, timeout):
        self.endpoint = endpoint
        self.timeout = timeout
        return self.browser


class FakePlaywright:
    def __init__(self, browser) -> None:
        self.chromium = FakeChromium(browser)

    async def stop(self) -> None:
        self.stopped = True


async def fake_pdf_render() -> None:
    playwright_service = infra.playwright.service
    playwright_params = infra.playwright.params
    old_endpoint = playwright_params.CDP_HEADLESS
    old_resolver = playwright_service.resolve_ws_url
    old_starter = playwright_service.start_playwright
    old_to_thread = asyncio.to_thread
    browser = FakeBrowser()
    driver = FakePlaywright(browser)
    playwright_params.CDP_HEADLESS = "http://renderer.test"
    playwright_service.resolve_ws_url = lambda endpoint, cache: "ws://renderer.test/devtools/browser/test"

    async def start_fake_playwright():
        return driver

    async def run_fake_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    playwright_service.start_playwright = start_fake_playwright
    asyncio.to_thread = run_fake_thread
    try:
        artifact = await domains.bd.distill.rendering.service.render_pdf("<html><body>Guide</body></html>")
        check("PDF renderer accepts a valid artifact", artifact.startswith(b"%PDF-"), True)
        check("PDF rendering disables page JavaScript and browser downloads",
              (browser.context_options["java_script_enabled"], browser.context_options["accept_downloads"]),
              (False, False))
        check("PDF rendering blocks network requests and uses print CSS",
              (browser.context.page.route_pattern, browser.context.page.pdf_options["prefer_css_page_size"]),
              ("**/*", True))
        check("PDF renderer releases its isolated browser context and driver",
              (browser.context.closed, driver.stopped), (True, True))
    finally:
        playwright_params.CDP_HEADLESS = old_endpoint
        playwright_service.resolve_ws_url = old_resolver
        playwright_service.start_playwright = old_starter
        asyncio.to_thread = old_to_thread


asyncio.run(fake_pdf_render())
