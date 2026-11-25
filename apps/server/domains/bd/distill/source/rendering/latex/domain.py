"""Build a safe, semantic Pandoc document from the validated report model."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import domains

from . import entities, params

_SAFE_MATH_COMMANDS = frozenset(
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi "
    "rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma "
    "Upsilon Phi Psi Omega forall exists neg lnot land lor top bot emptyset varnothing in notin ni subset "
    "subseteq supset supseteq cup cap setminus times cdot ast star circ pm mp div plusminus le leq ge geq "
    "neq ne equiv sim simeq approx propto cong parallel perp implies iff to mapsto rightarrow leftarrow "
    "leftrightarrow Rightarrow Leftarrow Leftrightarrow longrightarrow longleftarrow Longrightarrow "
    "Longleftarrow longleftrightarrow begin end "
    "sum prod coprod int iint iiint oint lim limsup liminf sup inf max min argmin argmax det gcd lcm "
    "sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh log ln lg exp deg dim ker Pr mod pmod "
    "sqrt frac dfrac tfrac binom dbinom tbinom overline underline bar hat widehat tilde widetilde vec "
    "dot ddot acute grave check breve mathrm mathbf mathit mathsf mathtt mathcal mathbb mathfrak "
    "boldsymbol operatorname text textrm textbf textit textsf texttt left right big Big bigg Bigg biggl "
    "Biggl biggr Biggr lvert rvert lVert rVert langle rangle lceil rceil lfloor rfloor "
    "quad qquad enspace thinspace hspace phantom vphantom smash mathop limits nolimits displaystyle "
    "textstyle scriptstyle scriptscriptstyle cases aligned alignedat gathered matrix pmatrix bmatrix Bmatrix "
    "vmatrix Vmatrix".split()
)
_SAFE_MATH_SYMBOL_COMMANDS = frozenset({"\\", "{", "}", "_", ",", ";", "!", " ", "&"})
_SAFE_MATH_ENVIRONMENTS = frozenset({
    "aligned", "alignedat", "gathered", "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "cases",
})
_MATH_COMMAND_RE = re.compile(r"\\([A-Za-z]+|.)")
_MATH_ENVIRONMENT_RE = re.compile(r"\\(begin|end)\s*\{([^{}]+)\}")


def _inlines(value: str) -> list[dict[str, Any]]:
    """Encode plain text as Pandoc inline nodes so source text is never raw markup."""
    result: list[dict[str, Any]] = []
    for token in re.findall(r"[^\s]+|\r\n|\r|\n|[^\S\r\n]+", value):
        if token in {"\n", "\r", "\r\n"}:
            result.append({"t": "SoftBreak"})
        elif token.isspace():
            result.append({"t": "Space"})
        elif token:
            result.append({"t": "Str", "c": token})
    return result


def _strong(value: str) -> dict[str, Any]:
    return {"t": "Strong", "c": _inlines(value)}


def _link(label: str, url: str) -> dict[str, Any]:
    return {"t": "Link", "c": [["", [], []], _inlines(label), [url, ""]]}


def _para(value: str | list[dict[str, Any]]) -> dict[str, Any]:
    return {"t": "Para", "c": _inlines(value) if isinstance(value, str) else value}


def _header(level: int, value: str) -> dict[str, Any]:
    return {"t": "Header", "c": [["", [], []], level, _inlines(value)]}


def _bullet_list(items: list[str | list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "t": "BulletList",
        "c": [[_para(item)] for item in items],
    }


def _ordered_list(items: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "t": "OrderedList",
        "c": [[1, {"t": "DefaultStyle"}, {"t": "DefaultDelim"}], [[_para(item)] for item in items]],
    }


def _safe_equation(value: str) -> bool:
    """Allow a conservative math subset; reject TeX commands with file or execution effects."""
    if not value or len(value) > 2_000 or any(character in value for character in "\x00$#") or "^^" in value:
        return False
    if value.count("{") != value.count("}"):
        return False
    environment_stack = []
    environments = []
    for operation, environment in _MATH_ENVIRONMENT_RE.findall(value):
        if environment not in _SAFE_MATH_ENVIRONMENTS:
            return False
        environments.append(environment)
        if operation == "begin":
            environment_stack.append(environment)
        elif not environment_stack or environment_stack.pop() != environment:
            return False
    if environment_stack:
        return False
    for command in _MATH_COMMAND_RE.findall(value):
        if command not in _SAFE_MATH_COMMANDS and command not in _SAFE_MATH_SYMBOL_COMMANDS:
            return False
    allowed_symbols = " \t\r\n{}[]()=+-*/_^.,:;|<>!&'`~\\"
    if any(not character.isalnum() and character not in allowed_symbols for character in value):
        return False
    if "&" in value and not any(environment in _SAFE_MATH_ENVIRONMENTS - {"cases"} for environment in environments):
        return False
    return True


def _math(value: str) -> dict[str, Any]:
    return {"t": "Math", "c": [{"t": "DisplayMath"}, value]}


def _source_label(reference: domains.bd.distill.ai.schemas.SourceRef) -> str:
    section = " › ".join(reference.section_path)
    parts = [f"Book page {reference.page_number}"]
    if section:
        parts.append(section)
    return " · ".join(parts)


def _evidence_blocks(
    evidence: list[domains.bd.distill.ai.schemas.EvidenceQuote] | list[domains.bd.distill.ai.schemas.SourceRef],
) -> list[dict[str, Any]]:
    if not evidence:
        return []
    return [
        _header(4, "Evidence excerpts"),
        _bullet_list([f"Page {item.page_number}: “{item.quote}”" for item in evidence]),
    ]


def _external_source_blocks(
    source_ids: list[str],
    external_sources: dict[str, domains.bd.distill.ai.schemas.ResearchSource],
) -> list[dict[str, Any]]:
    links = [
        _link(source.title, source.url)
        for source_id in dict.fromkeys(source_ids)
        if (source := external_sources.get(source_id)) is not None
    ]
    if not links:
        return []
    content: list[dict[str, Any]] = []
    for index, link in enumerate(links):
        if index:
            content.append({"t": "Str", "c": ";"})
            content.append({"t": "Space"})
        content.append(link)
    return [_para([_strong("External sources: "), *content])]


def _lesson_blocks(
    lesson: domains.bd.distill.ai.schemas.ReportLesson,
    external_sources: dict[str, domains.bd.distill.ai.schemas.ResearchSource],
) -> list[dict[str, Any]]:
    blocks = [
        _header(3, lesson.title),
        _para(lesson.explanation),
        _para([
            _strong("Attribution: "), *_inlines("Agent inference" if lesson.attribution == "agent_inference" else "Book claim"),
            {"t": "Str", "c": " · "},
            _strong("Evidence: "), *_inlines("verified" if lesson.verification_status == "verified" else "unverified"),
        ]),
        _header(4, "Source pages"),
        _bullet_list([f"{_source_label(reference)} — “{reference.quote}”" for reference in lesson.source_refs]),
    ]
    blocks.extend(_external_source_blocks(lesson.external_source_ids, external_sources))
    return blocks


def _visual_image(filename: str, alt: str) -> dict[str, Any]:
    image_path = f"assets/{filename.removesuffix('.svg')}.pdf" if filename.endswith(".svg") else f"assets/{filename}"
    return _para([{
        "t": "Image",
        "c": [["", [], []], _inlines(alt), [image_path, ""]],
    }])


def _safe_png_dimensions(image: bytes) -> bool:
    if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) < 24 or image[12:16] != b"IHDR":
        return False
    width = int.from_bytes(image[16:20], "big")
    height = int.from_bytes(image[20:24], "big")
    return width > 0 and height > 0 and width * height <= params.MAX_SOURCE_FIGURE_PIXELS


def _add_svg_asset(svg: str, assets: list[entities.ReportAsset], alt: str) -> dict[str, Any] | None:
    if not svg:
        return None
    content = svg.encode("utf-8")
    vector_asset_count = sum(asset.filename.endswith(".svg") for asset in assets)
    if (
        len(content) > params.MAX_SVG_BYTES
        or len(assets) >= params.MAX_ASSET_COUNT
        or vector_asset_count >= params.MAX_VECTOR_ASSET_COUNT
    ):
        return None
    filename = f"visual-{len(assets) + 1:04d}.svg"
    assets.append(entities.ReportAsset(filename, "image/svg+xml", content))
    return _visual_image(filename, alt)


def _add_source_figure(
    figure: domains.bd.distill.ai.schemas.ReportSourceFigure,
    source_figure_data: dict[str, bytes],
    assets: list[entities.ReportAsset],
) -> dict[str, Any] | None:
    image = source_figure_data.get(figure.figure_name)
    if (
        image is None
        or not _safe_png_dimensions(image)
        or len(image) > domains.bd.distill.source.rendering.params.MAX_INLINE_SOURCE_FIGURE_BYTES
        or len(assets) >= params.MAX_ASSET_COUNT
    ):
        return None
    filename = f"source-figure-{len(assets) + 1:04d}.png"
    assets.append(entities.ReportAsset(filename, "image/png", image))
    return _visual_image(filename, f"Book figure: {figure.caption}")


def _diagram_blocks(
    diagram: domains.bd.distill.ai.schemas.DiagramSpec,
    assets: list[entities.ReportAsset],
) -> list[dict[str, Any]]:
    blocks = [_header(3, diagram.title), _para(diagram.accessible_description)]
    image = _add_svg_asset(
        domains.bd.distill.source.rendering.domain.diagram_svg(diagram, f"latex-diagram-{len(assets) + 1}"),
        assets,
        diagram.accessible_description,
    )
    if image is not None:
        blocks.append(image)
    blocks.extend([
        _header(4, "Diagram concepts"),
        _bullet_list([
            f"{node.label} ({'agent inference' if node.attribution == 'agent_inference' else 'book claim'}); "
            + "; ".join(f"page {item.page_number}: “{item.quote}”" for item in node.evidence)
            for node in diagram.nodes
        ]),
        _header(4, "Relationships"),
        _ordered_list([[
            {"t": "Str", "c": next(node.label for node in diagram.nodes if node.node_id == edge.source_id)},
            {"t": "Space"}, {"t": "Str", "c": f"— {edge.relation.replace('_', ' ')} →"}, {"t": "Space"},
            {"t": "Str", "c": next(node.label for node in diagram.nodes if node.node_id == edge.target_id)},
            {"t": "Space"}, {"t": "Str", "c": f"(pages {', '.join(str(item.page_number) for item in edge.evidence)})"},
        ] for edge in diagram.edges]),
    ])
    return blocks


def _equation_blocks(equation: domains.bd.distill.ai.schemas.EquationBlock) -> list[dict[str, Any]]:
    blocks = [_header(3, equation.title)]
    if _safe_equation(equation.latex):
        blocks.append(_para([_math(equation.latex)]))
    else:
        blocks.extend([
            _para("The equation source contained unsupported TeX commands and is shown as literal text for safety."),
            _para(equation.latex),
        ])
    blocks.append(_para(equation.explanation))
    blocks.append(_para(f"Attribution: {'Agent inference' if equation.attribution == 'agent_inference' else 'Book claim'}"))
    if equation.assumptions:
        blocks.extend([_header(4, "Assumptions"), _bullet_list(equation.assumptions)])
    blocks.extend(_evidence_blocks(equation.evidence))
    return blocks


def _chart_blocks(
    chart: domains.bd.distill.ai.schemas.ChartSpec,
    assets: list[entities.ReportAsset],
) -> list[dict[str, Any]]:
    blocks = [
        _header(3, chart.title),
        _para(chart.caption),
        _para([_strong("How to read this visual: "), *_inlines(chart.accessible_description)]),
    ]
    image = _add_svg_asset(
        domains.bd.distill.source.rendering.domain.chart_svg(chart, f"latex-chart-{len(assets) + 1}"),
        assets,
        chart.accessible_description,
    )
    if image is not None:
        blocks.append(image)
    blocks.append(_header(4, chart.dataset.title))
    row_items = []
    for row_index, row in enumerate(chart.dataset.rows, start = 1):
        values = [f"{column.name}: {row.values.get(column.name, '')}" for column in chart.dataset.columns]
        evidence = "; ".join(f"p. {item.page_number}: “{item.quote}”" for item in row.evidence)
        row_items.append(f"Row {row_index} — " + " · ".join(values) + f". Evidence: {evidence}")
    blocks.append(_ordered_list([_inlines(item) for item in row_items]))
    if chart.dataset.transformations:
        blocks.append(_para([_strong("Transformations: "), *_inlines("; ".join(chart.dataset.transformations))]))
    if chart.dataset.assumptions:
        blocks.extend([_header(4, "Data assumptions"), _bullet_list(chart.dataset.assumptions)])
    return blocks


def _timeline_blocks(timeline: domains.bd.distill.ai.schemas.ReportTimeline) -> list[dict[str, Any]]:
    blocks = [_header(3, timeline.title), _para(timeline.accessible_description)]
    items = []
    for event in timeline.events:
        evidence = "; ".join(f"p. {item.page_number}: “{item.quote}”" for item in event.source_refs)
        items.append(_inlines(
            f"{event.date_or_order} — {event.label}: {event.description} "
            f"({'agent inference' if event.attribution == 'agent_inference' else 'book claim'}). Evidence: {evidence}"
        ))
    blocks.append(_ordered_list(items))
    return blocks


def _map_blocks(
    map_spec: domains.bd.distill.ai.schemas.GeoMapSpec,
    assets: list[entities.ReportAsset],
) -> list[dict[str, Any]]:
    blocks = [_header(3, map_spec.title), _para(map_spec.accessible_description)]
    image = _add_svg_asset(
        domains.bd.distill.source.rendering.domain.map_svg(map_spec, f"latex-map-{len(assets) + 1}"),
        assets,
        map_spec.accessible_description,
    )
    if image is not None:
        blocks.append(image)
    items = []
    for feature in map_spec.geojson.features:
        properties = feature.properties
        evidence = "; ".join(
            f"p. {item.page_number}: “{item.quote}”" for item in properties.evidence
        )
        items.append(
            f"{properties.label}: {properties.description or 'No additional description.'} "
            f"({'agent inference' if properties.attribution == 'agent_inference' else 'book claim'}). "
            f"Evidence: {evidence}"
        )
    blocks.append(_bullet_list(items))
    return blocks


def _lesson_list(
    title: str,
    lessons: list[domains.bd.distill.ai.schemas.ReportLesson],
    external_sources: dict[str, domains.bd.distill.ai.schemas.ResearchSource],
) -> list[dict[str, Any]]:
    if not lessons:
        return []
    blocks = [_header(2, title)]
    for lesson in lessons:
        blocks.extend(_lesson_blocks(lesson, external_sources))
    return blocks


def build_document(
    report: domains.bd.distill.ai.schemas.StudyGuideDocument,
    source_figure_data: dict[str, bytes] | None = None,
) -> entities.LatexDocument:
    """Convert the report into structured Pandoc JSON and local image assets."""
    source_figure_data = source_figure_data or {}
    assets: list[entities.ReportAsset] = []
    blocks: list[dict[str, Any]] = []
    external_sources = {source.source_id: source for source in report.research_context.sources}

    blocks.extend([
        _header(1, "Executive overview"),
        _para(
            f"{len(report.chapters)} chapters · {report.source_page_count} source pages · "
            f"{report.selected_page_count} selected pages · {len(report.covered_pages)} covered pages · "
            f"{len(report.uncovered_pages)} uncovered pages"
        ),
        _header(2, "Central thesis"),
        _para(report.central_thesis),
        _para(f"Attribution: {'Agent inference' if report.central_thesis_attribution == 'agent_inference' else 'Book claim'}"),
        _header(3, "Source pages"),
        _bullet_list([f"{_source_label(reference)} — “{reference.quote}”" for reference in report.central_thesis_source_refs]),
    ])

    if report.decision_brief is not None:
        decision = report.decision_brief
        blocks.extend([_header(2, "Decision brief"), _header(3, "Decision question"), _para(decision.decision_question)])
        blocks.append(_header(3, "Recommendation"))
        blocks.extend(_lesson_blocks(decision.recommendation, external_sources))
        if decision.options:
            blocks.append(_header(3, "Options"))
            for option in decision.options:
                blocks.extend([
                    _header(4, option.title),
                    _para(option.expected_outcome),
                    _para(f"Attribution: {'Agent inference' if option.attribution == 'agent_inference' else 'Book claim'}"),
                    _para([_strong("Trade-offs: "), *_inlines("; ".join(option.tradeoffs) or "Not specified")]),
                    _para([_strong("Risks: "), *_inlines("; ".join(option.risks) or "Not specified")]),
                    _bullet_list([f"{_source_label(item)} — “{item.quote}”" for item in option.source_refs]),
                ])
                blocks.extend(_external_source_blocks(option.external_source_ids, external_sources))
        if decision.assumptions:
            blocks.extend([_header(3, "Assumptions"), _bullet_list(decision.assumptions)])
        if decision.risks:
            blocks.extend([_header(3, "Risks"), _bullet_list(decision.risks)])
        if decision.action_plan:
            blocks.append(_header(3, "Action plan"))
            for step in decision.action_plan:
                blocks.extend([
                    _header(4, f"Step {step.sequence}: {step.action}"),
                    _para(step.rationale),
                    _para(f"Attribution: {'Agent inference' if step.attribution == 'agent_inference' else 'Book claim'}"),
                    _para([_strong("Success measure: "), *_inlines(step.success_measure)]),
                    _bullet_list([_source_label(item) + f" — “{item.quote}”" for item in step.source_refs]),
                ])
                if step.prerequisites:
                    blocks.extend([_header(5, "Prerequisites"), _bullet_list(step.prerequisites)])
                blocks.extend(_external_source_blocks(step.external_source_ids, external_sources))

    if report.research_context.status != "disabled":
        research = report.research_context
        blocks.extend([
            _header(1, "Current context and further reading"),
            _para({
                "unsupported_provider": "The selected search connection is not supported by this Distill MCP adapter.",
                "unavailable": "External search was unavailable; book-grounded lessons are unaffected.",
                "no_results": "External search returned no usable sources.",
                "complete": "External references add current context; they do not establish claims attributed to the book.",
            }.get(research.status, "External research is separate from evidence quoted from the book.")),
        ])
        if research.findings:
            blocks.append(_header(2, "Research findings"))
            for finding in research.findings:
                blocks.extend([
                    _header(3, finding.title),
                    _para(finding.summary),
                    _para([_strong("Relation to the book: "), *_inlines(f"{finding.relation_to_book} — {finding.relevance_to_book}")]),
                ])
                if finding.evidence_quotes:
                    blocks.extend([
                        _header(4, "Verified source passages"),
                        _bullet_list([
                            f"{quote.quote} — {quote.locator or 'Source passage'}"
                            for quote in finding.evidence_quotes
                        ]),
                    ])
                blocks.extend(_external_source_blocks(finding.source_ids, external_sources))
        if research.sources:
            blocks.append(_header(2, "Sources reviewed"))
            for source in research.sources:
                accessed = datetime.fromtimestamp(source.accessed_at, timezone.utc).date().isoformat()
                content = [
                    _header(3, source.title),
                    _para([_link(source.title, source.url)]),
                    _para(source.snippet),
                    _para(
                        f"{source.publisher or source.provider_id} · Accessed {accessed}"
                        + (f" · Published {source.published_at}" if source.published_at else "")
                        + (f" · Query: {source.query}" if source.query else "")
                    ),
                ]
                blocks.extend(content)

    if report.key_takeaways or report.cross_chapter_connections:
        blocks.append(_header(1, "Whole-book synthesis"))
        blocks.extend(_lesson_list("Key takeaways", report.key_takeaways, external_sources))
        blocks.extend(_lesson_list("Connections across chapters", report.cross_chapter_connections, external_sources))

    for chapter in report.chapters:
        blocks.extend([
            _header(1, chapter.title),
            _para(chapter.overview),
            _para(f"Attribution: {'Agent inference' if chapter.overview_attribution == 'agent_inference' else 'Book claim'}"),
            _header(3, "Overview source pages"),
            _bullet_list([f"{_source_label(reference)} — “{reference.quote}”" for reference in chapter.overview_source_refs]),
        ])
        if chapter.coverage_review_status != "unreviewed" or chapter.coverage_review_notes:
            blocks.append(_para([
                _strong("Coverage review: "), *_inlines(chapter.coverage_review_status.replace("_", " ")),
            ]))
            if chapter.coverage_review_notes:
                blocks.append(_bullet_list(chapter.coverage_review_notes))
        if chapter.learning_objectives:
            blocks.extend([_header(2, "Learning objectives"), _bullet_list([
                f"{item.outcome} ({item.kind}; importance {item.importance}/3; objective {item.objective_id}; "
                f"pages {', '.join(str(reference.page_number) for reference in item.source_refs)})"
                for item in chapter.learning_objectives
            ])])
        blocks.extend(_lesson_list("Lessons", chapter.lessons, external_sources))
        blocks.extend(_lesson_list("Open questions", chapter.open_questions, external_sources))
        if chapter.source_figures:
            blocks.append(_header(2, "Figures from the book"))
            for figure in chapter.source_figures:
                blocks.append(_header(3, f"Figure from page {figure.page_number}"))
                blocks.append(_para(figure.caption))
                image = _add_source_figure(figure, source_figure_data, assets)
                if image is not None:
                    blocks.append(image)
                blocks.append(_para(f"Source page {figure.page_number}."))
        if chapter.practice_items:
            blocks.append(_header(2, "Practice with feedback"))
            for practice in chapter.practice_items:
                blocks.extend([
                    _header(3, practice.question),
                    _para([_strong("Answer: "), *_inlines(practice.answer)]),
                    _para([_strong("Feedback: "), *_inlines(practice.feedback)]),
                    _para(f"{practice.kind.title()} · difficulty {practice.difficulty}/5"),
                    _bullet_list([
                        _source_label(reference) + f" — “{reference.quote}”"
                        for reference in practice.source_refs
                    ]),
                ])
        if chapter.diagrams:
            blocks.append(_header(2, "Concept diagrams"))
            for diagram in chapter.diagrams:
                blocks.extend(_diagram_blocks(diagram, assets))
        if chapter.equations:
            blocks.append(_header(2, "Equations"))
            for equation in chapter.equations:
                blocks.extend(_equation_blocks(equation))
        if chapter.charts:
            blocks.append(_header(2, "Charts and data"))
            for chart in chapter.charts:
                blocks.extend(_chart_blocks(chart, assets))
        if chapter.timelines:
            blocks.append(_header(2, "Timelines"))
            for timeline in chapter.timelines:
                blocks.extend(_timeline_blocks(timeline))
        if chapter.maps:
            blocks.append(_header(2, "Maps"))
            for map_spec in chapter.maps:
                blocks.extend(_map_blocks(map_spec, assets))

    if report.diagrams or report.equations or report.charts or report.maps:
        blocks.append(_header(1, "Whole-book visual references"))
        for diagram in report.diagrams:
            blocks.extend(_diagram_blocks(diagram, assets))
        for equation in report.equations:
            blocks.extend(_equation_blocks(equation))
        for chart in report.charts:
            blocks.extend(_chart_blocks(chart, assets))
        for map_spec in report.maps:
            blocks.extend(_map_blocks(map_spec, assets))

    if report.glossary:
        blocks.append(_header(1, "Glossary"))
        for entry in report.glossary:
            blocks.extend([
                _header(2, entry.term),
                _para(entry.definition),
                _para(f"Attribution: {'Agent inference' if entry.attribution == 'agent_inference' else 'Book claim'}"),
                _bullet_list([
                    _source_label(reference) + f" — “{reference.quote}”"
                    for reference in entry.source_refs
                ]),
            ])
    if report.limitation_notes:
        blocks.append(_header(1, "Limitations"))
        blocks.extend(_lesson_list("Limitations", report.limitation_notes, external_sources))
    if report.uncovered_pages:
        blocks.extend([
            _header(1, "Coverage and limitations"),
            _para(
                f"The report covers {len(report.covered_pages)} referenced pages; "
                f"{len(report.uncovered_pages)} pages were marked uncovered."
            ),
            _para("Uncovered pages: " + ", ".join(str(page) for page in report.uncovered_pages)),
        ])
    if report.review_notes:
        blocks.extend([_header(1, "Editorial notes"), _header(2, "Review notes"), _bullet_list(report.review_notes)])
    if report.review_required:
        if not report.review_notes:
            blocks.append(_header(1, "Editorial notes"))
        blocks.extend([
            _header(2, "Review status"),
            _para("This report is marked for human review. Check the coverage notes and evidence citations before relying on it for consequential decisions."),
        ])

    generated_date = datetime.fromtimestamp(report.generated_at, timezone.utc).date().isoformat()
    document = {
        "pandoc-api-version": params.PANDOC_API_VERSION,
        "meta": {
            "title": {"t": "MetaInlines", "c": _inlines(report.title)},
            "author": {"t": "MetaList", "c": [{"t": "MetaInlines", "c": _inlines("BD Distill · COELHO Nexus")}]},
            "date": {"t": "MetaString", "c": generated_date},
            "lang": {"t": "MetaString", "c": "en-US"},
        },
        "blocks": blocks,
    }
    body = json.dumps(document, ensure_ascii = False, separators = (",", ":"))
    encoded_size = len(body.encode("utf-8"))
    asset_bytes = sum(len(asset.content) for asset in assets)
    if encoded_size > params.MAX_SOURCE_JSON_BYTES:
        raise ValueError("Learning Guide is too large for LaTeX rendering")
    if len(assets) > params.MAX_ASSET_COUNT or asset_bytes > params.MAX_ASSET_BYTES:
        raise ValueError("Learning Guide visual assets exceed the LaTeX render limit")
    return entities.LatexDocument(source_json = body, assets = tuple(assets))
