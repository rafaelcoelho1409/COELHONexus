"""Pure transformations and validation rules for Book Distiller's Distill feature."""
from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Iterable, Iterator
from json import JSONDecodeError, JSONDecoder, loads
from typing import Any

from json_repair import loads as json_repair_loads
from pydantic import ValidationError

from . import entities, params, schemas

_WHITESPACE_RE = re.compile(r"\s+")


def learning_extraction_from_agent_result(result: Any) -> schemas.LearningExtraction | None:
    """Read a typed extraction, recovering valid JSON when the agent omitted its structured field."""
    if not isinstance(result, dict):
        return None

    for candidate in (result.get("structured_response"), result.get("parsed")):
        extraction = _validate_learning_extraction(candidate)
        if extraction is not None:
            return extraction

    raw = result.get("raw")
    for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
        for candidate in _tool_call_arguments(message):
            extraction = _validate_learning_extraction(candidate)
            if extraction is not None:
                return extraction
        content = _assistant_content(message)
        for candidate in _json_objects(content):
            extraction = _validate_learning_extraction(candidate)
            if extraction is not None:
                return extraction
    return None


def chapter_synthesis_from_agent_result(result: Any) -> schemas.ChapterSynthesis | None:
    """Parse a chapter guide from provider output, including a single known wrapper object."""
    if not isinstance(result, dict):
        return None

    for candidate in (result.get("structured_response"), result.get("parsed")):
        synthesis = _validate_chapter_synthesis(candidate)
        if synthesis is not None:
            return synthesis

    raw = result.get("raw")
    for message in [*(result.get("messages") or []), *([raw] if raw is not None else [])]:
        for candidate in _tool_call_arguments(message):
            synthesis = _validate_chapter_synthesis(candidate)
            if synthesis is not None:
                return synthesis
        content = _assistant_content(message)
        for candidate in _json_objects(content):
            synthesis = _validate_chapter_synthesis(candidate)
            if synthesis is not None:
                return synthesis
    return None


def _validate_chapter_synthesis(value: Any) -> schemas.ChapterSynthesis | None:
    for candidate in _chapter_synthesis_candidates(value):
        try:
            return schemas.ChapterSynthesis.model_validate(candidate)
        except ValidationError:
            continue
    return None


def _chapter_synthesis_candidates(value: Any) -> Iterator[dict[str, Any]]:
    """Yield the root response and unwrap only a single recognized provider envelope."""
    candidate = value
    for _ in range(4):
        if not isinstance(candidate, dict):
            return
        yield candidate
        if len(candidate) != 1:
            return
        key, nested = next(iter(candidate.items()))
        if key not in {"chapter", "output", "result", "section", "synthesis"} or not isinstance(nested, dict):
            return
        candidate = nested


def _validate_learning_extraction(value: Any) -> schemas.LearningExtraction | None:
    if value is None:
        return None
    try:
        return schemas.LearningExtraction.model_validate(value)
    except ValidationError:
        return None


def _assistant_content(message: Any) -> str | None:
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
        content = message.get("content")
    else:
        role = getattr(message, "type", None)
        content = getattr(message, "content", None)
    if role not in {"assistant", "ai", "AIMessage"}:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def _tool_call_arguments(message: Any) -> Iterator[dict[str, Any]]:
    for call in getattr(message, "tool_calls", None) or []:
        if isinstance(call, dict) and isinstance(call.get("args"), dict):
            yield call["args"]

    additional = getattr(message, "additional_kwargs", None) or {}
    calls = additional.get("tool_calls") if isinstance(additional, dict) else None
    for call in calls or []:
        function = call.get("function") if isinstance(call, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if isinstance(arguments, dict):
            yield arguments
        elif isinstance(arguments, str):
            try:
                value = loads(arguments)
            except JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _json_objects(content: str | None) -> Iterator[dict[str, Any]]:
    if not content:
        return
    try:
        repaired = json_repair_loads(content)
    except Exception:  # noqa: BLE001 — strict parsing below can still recover a later JSON object.
        repaired = None
    if isinstance(repaired, dict):
        yield repaired
    decoder = JSONDecoder()
    for start, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[start:])
        except JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]


def split_source_page(
    markdown: str,
    *,
    page_number: int,
    source_key: str,
    source_sha256: str,
    section_path: list[str],
    max_chars: int = params.MAX_SOURCE_UNIT_CHARS,
) -> list[tuple[entities.UnitRef, str]]:
    """Split one page at whitespace boundaries, retaining physical-page offsets and provenance."""
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    pieces: list[tuple[entities.UnitRef, str]] = []
    start = 0
    part = 0
    while start < len(markdown):
        while start < len(markdown) and markdown[start].isspace():
            start += 1
        if start >= len(markdown):
            break
        end = min(start + max_chars, len(markdown))
        if end < len(markdown):
            boundary = max(markdown.rfind("\n\n", start, end), markdown.rfind(" ", start, end), markdown.rfind("\n", start, end))
            if boundary > start + max_chars // 2:
                end = boundary
        text = markdown[start:end].strip()
        if not text:
            start = max(end, start + 1)
            continue
        actual_start = markdown.find(text, start, end)
        actual_end = actual_start + len(text)
        unit_id = stable_id(str(page_number), str(part), source_sha256, sha256_text(text))
        ref: entities.UnitRef = {
            "unit_id": unit_id,
            "input_key": "",
            "page_number": page_number,
            "section_path": list(section_path),
            "source_sha256": source_sha256,
            "text_sha256": sha256_text(text),
            "char_start": actual_start,
            "char_end": actual_end,
        }
        pieces.append((ref, text))
        start = actual_end
        part += 1
    return pieces


def quote_is_supported(quote: str, source_text: str) -> bool:
    """Check that a citation quote resolves to the supplied source after whitespace normalization."""
    normalized_quote = _WHITESPACE_RE.sub(" ", quote).strip()
    normalized_source = _WHITESPACE_RE.sub(" ", source_text).strip()
    return bool(normalized_quote) and normalized_quote in normalized_source


def group_by_section(
    refs: Iterable[entities.UnitRef],
) -> list[tuple[tuple[str, ...], list[entities.UnitRef]]]:
    groups: dict[tuple[str, ...], list[entities.UnitRef]] = {}
    for ref in refs:
        path = tuple(part.strip() for part in ref["section_path"] if part.strip()) or ("Unassigned material",)
        groups.setdefault(path, []).append(ref)
    return sorted(groups.items(), key = lambda item: min(ref["page_number"] for ref in item[1]))


def validate_synthesis_unit_ids(unit_ids: Iterable[str], available_ids: set[str]) -> list[str]:
    selected = list(dict.fromkeys(unit_ids))
    if not selected:
        raise ValueError("synthesis must cite at least one extracted learning unit")
    missing = set(selected) - available_ids
    if missing:
        raise ValueError("synthesis referenced unknown learning unit IDs")
    return selected


def _svg_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _chart_svg(chart: schemas.ChartSpec, identifier: str) -> str:
    if chart.chart_type == "table":
        return ""
    numeric_rows = [
        (row, row.values.get(chart.x_field))
        for row in chart.dataset.rows
        if any(isinstance(row.values.get(field), (int, float)) for field in chart.y_fields)
    ]
    if not numeric_rows:
        return ""

    width, height = 760, 360
    left, right, top, bottom = 72, 24, 30, 90
    plot_width, plot_height = width - left - right, height - top - bottom
    values = [
        float(row.values[field])
        for row, _ in numeric_rows
        for field in chart.y_fields
        if isinstance(row.values.get(field), (int, float)) and not isinstance(row.values.get(field), bool)
    ]
    if not values:
        return ""
    minimum, maximum = min(0.0, min(values)), max(0.0, max(values))
    if minimum == maximum:
        maximum = minimum + 1
    y = lambda value: top + (maximum - value) * plot_height / (maximum - minimum)
    baseline = y(0.0)
    colors = ("#245b91", "#9c4c16", "#32734d", "#7c4b8e")
    parts = [
        (
            f'<svg class="report-chart" viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="{identifier}-title {identifier}-description">'
        ),
        f'<title id="{identifier}-title">{html.escape(chart.title)}</title>',
        f'<desc id="{identifier}-description">{html.escape(chart.accessible_description)}</desc>',
    ]
    for tick in range(5):
        tick_value = minimum + (maximum - minimum) * tick / 4
        tick_y = y(tick_value)
        parts.append(
            f'<line x1="{left}" y1="{_svg_number(tick_y)}" x2="{width - right}" '
            f'y2="{_svg_number(tick_y)}" stroke="#d1d5db" />'
            f'<text x="{left - 10}" y="{_svg_number(tick_y + 4)}" text-anchor="end" '
            f'font-size="12">{html.escape(_svg_number(tick_value))}</text>'
        )
    parts.append(
        f'<line x1="{left}" y1="{_svg_number(baseline)}" x2="{width - right}" '
        f'y2="{_svg_number(baseline)}" stroke="#374151" />'
    )
    if chart.chart_type == "bar":
        slot = plot_width / len(numeric_rows)
        bar_width = min(44.0, slot * 0.72 / max(1, len(chart.y_fields)))
        for row_index, (row, x_value) in enumerate(numeric_rows):
            center = left + slot * (row_index + 0.5)
            label = "" if x_value is None else str(x_value)
            parts.append(
                f'<text x="{_svg_number(center)}" y="{height - bottom + 20}" text-anchor="middle" '
                f'font-size="11"><title>{html.escape(label)}</title>{html.escape(label[:18])}</text>'
            )
            for series_index, field in enumerate(chart.y_fields):
                value = row.values.get(field)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                bar_x = center + (series_index - (len(chart.y_fields) - 1) / 2) * bar_width - bar_width / 2
                bar_y = min(y(float(value)), baseline)
                bar_height = max(1.0, abs(baseline - y(float(value))))
                parts.append(
                    f'<rect x="{_svg_number(bar_x)}" y="{_svg_number(bar_y)}" '
                    f'width="{_svg_number(bar_width)}" height="{_svg_number(bar_height)}" '
                    f'fill="{colors[series_index % len(colors)]}"><title>{html.escape(field)}: '
                    f'{html.escape(str(value))}</title></rect>'
                )
    else:
        x_column = next(column for column in chart.dataset.columns if column.name == chart.x_field)
        raw_x = [value for _, value in numeric_rows]
        numeric_x = x_column.value_type == "number" and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in raw_x
        )
        if numeric_x:
            x_values = [float(value) for value in raw_x]
            x_min, x_max = min(x_values), max(x_values)
            if x_min == x_max:
                x_min, x_max = x_min - 1, x_max + 1
            x_positions = [left + (value - x_min) * plot_width / (x_max - x_min) for value in x_values]
        else:
            x_positions = [left + plot_width * (index + 0.5) / len(numeric_rows) for index in range(len(numeric_rows))]
        for series_index, field in enumerate(chart.y_fields):
            points = []
            for (row, x_value), x_position in zip(numeric_rows, x_positions, strict = True):
                value = row.values.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    points.append((x_position, y(float(value)), x_value, value))
            color = colors[series_index % len(colors)]
            if chart.chart_type == "line" and len(points) > 1:
                coords = " ".join(f"{_svg_number(x_pos)},{_svg_number(y_pos)}" for x_pos, y_pos, _, _ in points)
                parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="3" />')
            for x_pos, y_pos, x_value, value in points:
                parts.append(
                    f'<circle cx="{_svg_number(x_pos)}" cy="{_svg_number(y_pos)}" r="5" fill="{color}">'
                    f'<title>{html.escape(str(x_value))}, {html.escape(field)}: {html.escape(str(value))}</title></circle>'
                )
            if not numeric_x:
                for index, x_value in enumerate(raw_x):
                    if series_index == 0:
                        label = "" if x_value is None else str(x_value)
                        parts.append(
                            f'<text x="{_svg_number(x_positions[index])}" y="{height - bottom + 20}" '
                            f'text-anchor="middle" font-size="11"><title>{html.escape(label)}</title>'
                            f'{html.escape(label[:18])}</text>'
                        )
    for index, field in enumerate(chart.y_fields):
        x_pos = left + index * min(190, plot_width / max(1, len(chart.y_fields)))
        parts.append(
            f'<rect x="{_svg_number(x_pos)}" y="{height - 25}" width="10" height="10" '
            f'fill="{colors[index % len(colors)]}" /><text x="{_svg_number(x_pos + 15)}" '
            f'y="{height - 16}" font-size="12">{html.escape(field)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _diagram_svg(diagram: schemas.DiagramSpec, identifier: str) -> str:
    width, columns, node_width, node_height = 760, 4, 160, 68
    rows = (len(diagram.nodes) + columns - 1) // columns
    height = max(150, rows * 132 + 32)
    positions = {
        node.node_id: (
            20 + (index % columns) * 185 + node_width / 2,
            20 + (index // columns) * 132 + node_height / 2,
        )
        for index, node in enumerate(diagram.nodes)
    }
    parts = [
        (
            f'<svg class="report-diagram" viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="{identifier}-title {identifier}-description">'
        ),
        f'<title id="{identifier}-title">{html.escape(diagram.title)}</title>',
        f'<desc id="{identifier}-description">{html.escape(diagram.accessible_description)}</desc>',
        (
            f'<defs><marker id="{identifier}-arrow" markerWidth="8" markerHeight="8" refX="7" '
            'refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#4b5563" /></marker></defs>'
        ),
    ]
    for edge in diagram.edges:
        start_x, start_y = positions[edge.source_id]
        end_x, end_y = positions[edge.target_id]
        delta_x, delta_y = end_x - start_x, end_y - start_y
        if delta_x == 0 and delta_y == 0:
            loop_x = start_x + node_width / 2
            parts.append(
                f'<path d="M {_svg_number(loop_x)} {_svg_number(start_y - 14)} '
                f'C {_svg_number(loop_x + 20)} {_svg_number(start_y - 14)}, '
                f'{_svg_number(loop_x + 20)} {_svg_number(start_y + 14)}, '
                f'{_svg_number(loop_x)} {_svg_number(start_y + 14)}" fill="none" '
                f'stroke="#4b5563" marker-end="url(#{identifier}-arrow)" />'
                f'<text x="{_svg_number(loop_x + 18)}" y="{_svg_number(start_y - 20)}" '
                f'text-anchor="middle" font-size="11">{html.escape(edge.relation.replace("_", " "))}</text>'
            )
            continue
        start_scale = min(
            (node_width / 2) / abs(delta_x) if delta_x else float("inf"),
            (node_height / 2) / abs(delta_y) if delta_y else float("inf"),
        )
        end_scale = min(
            (node_width / 2) / abs(delta_x) if delta_x else float("inf"),
            (node_height / 2) / abs(delta_y) if delta_y else float("inf"),
        )
        x1, y1 = start_x + delta_x * start_scale, start_y + delta_y * start_scale
        x2, y2 = end_x - delta_x * end_scale, end_y - delta_y * end_scale
        parts.append(
            f'<line x1="{_svg_number(x1)}" y1="{_svg_number(y1)}" '
            f'x2="{_svg_number(x2)}" y2="{_svg_number(y2)}" stroke="#4b5563" '
            f'marker-end="url(#{identifier}-arrow)" />'
            f'<text x="{_svg_number((x1 + x2) / 2)}" y="{_svg_number((y1 + y2) / 2 - 5)}" '
            f'text-anchor="middle" font-size="11">{html.escape(edge.relation.replace("_", " "))}</text>'
        )
    for node in diagram.nodes:
        center_x, center_y = positions[node.node_id]
        left_x, top_y = center_x - node_width / 2, center_y - node_height / 2
        label = node.label if len(node.label) <= 28 else node.label[:27] + "…"
        parts.append(
            f'<rect x="{_svg_number(left_x)}" y="{_svg_number(top_y)}" width="{node_width}" '
            f'height="{node_height}" rx="8" fill="#f3f4f6" stroke="#4b5563" />'
            f'<text x="{_svg_number(center_x)}" y="{_svg_number(center_y + 5)}" text-anchor="middle" '
            f'font-size="13"><title>{html.escape(node.label)}</title>{html.escape(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_html(report: schemas.StudyGuideDocument) -> str:
    """Render a static, script-free Learning Guide from the validated report model."""
    escape = html.escape

    def source_links(source_refs: list[schemas.SourceRef]) -> str:
        return " ".join(
            f'<a href="/api/v1/bd/ingestion/{escape(report.book_id)}/agentic/runs/{escape(ref.ingestion_ai_run_id)}/pages/{ref.page_number}">p. {ref.page_number}</a>'
            for ref in source_refs
        )

    def lesson_html(lesson: schemas.ReportLesson) -> str:
        attribution = "Agent inference" if lesson.attribution == "agent_inference" else "Book claim"
        verification = "Verified evidence" if lesson.verification_status == "verified" else "Needs evidence review"
        quotes = "".join(
            "<li><a href=\"/api/v1/bd/ingestion/" + escape(report.book_id) + "/agentic/runs/"
            + escape(ref.ingestion_ai_run_id) + "/pages/" + str(ref.page_number)
            + "\">Page " + str(ref.page_number) + "</a>: <q>" + escape(ref.quote) + "</q></li>"
            for ref in lesson.source_refs
        )
        return (
            "<article><h4>" + escape(lesson.title) + "</h4><p>" + escape(lesson.explanation).replace("\n", "<br>")
            + "</p><p class=\"sources\">" + attribution + " · " + verification + " · " + source_links(lesson.source_refs)
            + "</p><details><summary>Evidence excerpts</summary><ul>" + quotes + "</ul></details></article>"
        )

    def evidence_links(evidence) -> str:
        return " ".join(
            f'<a href="/api/v1/bd/ingestion/{escape(report.book_id)}/agentic/runs/{escape(report.ingestion_ai_run_id)}/pages/{item.page_number}">p. {item.page_number}</a>'
            for item in evidence
        )

    chapters: list[str] = []
    for index, chapter in enumerate(report.chapters, start = 1):
        lessons = "".join(lesson_html(lesson) for lesson in chapter.lessons)
        questions = "".join(
            "<li><strong>" + escape(item.title) + "</strong>: " + escape(item.explanation)
            + (" <em>(agent inference)</em>" if item.attribution == "agent_inference" else "") + "</li>"
            for item in chapter.open_questions
        )
        diagrams = "".join(
            "<article><h3>" + escape(diagram.title) + "</h3><p>" + escape(diagram.accessible_description)
            + "</p>" + _diagram_svg(diagram, f"diagram-{index}-{diagram_index}") + "<ul>" + "".join(
                "<li>" + ("Agent inference: " if node.attribution == "agent_inference" else "")
                + escape(node.label) + " <span class=\"sources\">" + evidence_links(node.evidence)
                + "</span></li>" for node in diagram.nodes
            ) + "</ul>"
            + "<ol>" + "".join(
                "<li>" + escape(next((node.label for node in diagram.nodes if node.node_id == edge.source_id), edge.source_id))
                + " — " + escape(edge.relation.replace("_", " ")) + " → "
                + escape(next((node.label for node in diagram.nodes if node.node_id == edge.target_id), edge.target_id))
                + (" (agent inference)" if edge.attribution == "agent_inference" else "")
                + " <span class=\"sources\">" + evidence_links(edge.evidence) + "</span></li>" for edge in diagram.edges
            ) + "</ol></article>"
            for diagram_index, diagram in enumerate(chapter.diagrams, start = 1)
        )
        equations = "".join(
            "<article><h3>" + escape(equation.title) + "</h3><pre>" + escape(equation.latex)
            + "</pre><p>" + escape(equation.explanation)
            + (" <strong>(agent inference)</strong>" if equation.attribution == "agent_inference" else "")
            + "</p><p class=\"sources\">Evidence: "
            + evidence_links(equation.evidence) + "</p></article>"
            for equation in chapter.equations
        )
        charts = "".join(
            "<figure><figcaption><strong>" + escape(chart.title) + "</strong><p>" + escape(chart.caption)
            + "</p><p>" + escape(chart.accessible_description) + "</p></figcaption>"
            + _chart_svg(chart, f"chart-{index}-{chart_index}")
            + "<table><thead><tr>"
            + "".join(
                "<th scope=\"col\">" + escape(column.name + (f" ({column.unit})" if column.unit else "")) + "</th>"
                for column in chart.dataset.columns
            ) + "<th scope=\"col\">Sources</th></tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join(
                    "<td>" + escape(str(row.values.get(column.name, ""))) + "</td>"
                    for column in chart.dataset.columns
                ) + "<td>" + evidence_links(row.evidence) + "</td></tr>"
                for row in chart.dataset.rows
            ) + "</tbody></table><p class=\"sources\">" + escape(chart.dataset.title)
            + (" · Transformations: " + escape("; ".join(chart.dataset.transformations)) if chart.dataset.transformations else "")
            + "</p></figure>"
            for chart_index, chart in enumerate(chapter.charts, start = 1)
        )
        chapters.append(
            f'<section id="chapter-{index}"><h2>{escape(chapter.title)}</h2>'
            f'<p>{"<strong>Agent inference:</strong> " if chapter.overview_attribution == "agent_inference" else ""}'
            f'{escape(chapter.overview).replace(chr(10), "<br>")}</p>'
            f'<p class="sources">Sources: {source_links(chapter.overview_source_refs)}</p>{lessons}'
            f'<h3>Recall questions</h3><ol>{questions}</ol>{diagrams}{equations}{charts}</section>'
        )
    takeaways = "".join(
        "<li>" + lesson_html(lesson) + "</li>"
        for lesson in report.key_takeaways
    )
    connections = "".join(
        "<li>" + lesson_html(lesson) + "</li>"
        for lesson in report.cross_chapter_connections
    )
    limitations = "".join(
        "<li>" + lesson_html(item) + "</li>" for item in report.limitation_notes
    )
    glossary = "".join(
        "<dt>" + escape(item.term) + "</dt><dd>" + escape(item.definition)
        + (" <strong>(agent inference)</strong>" if item.attribution == "agent_inference" else "")
        + " <span class=\"sources\">" + source_links(item.source_refs) + "</span></dd>"
        for item in report.glossary
    )
    report_diagrams = "".join(
        "<article><h3>" + escape(diagram.title) + "</h3><p>" + escape(diagram.accessible_description)
        + "</p>" + _diagram_svg(diagram, f"book-diagram-{index}") + "<ul>" + "".join(
            "<li>" + ("Agent inference: " if node.attribution == "agent_inference" else "")
            + escape(node.label) + " <span class=\"sources\">" + evidence_links(node.evidence)
            + "</span></li>" for node in diagram.nodes
        ) + "</ul><ol>" + "".join(
            "<li>" + escape(next((node.label for node in diagram.nodes if node.node_id == edge.source_id), edge.source_id))
            + " — " + escape(edge.relation.replace("_", " ")) + " → "
            + escape(next((node.label for node in diagram.nodes if node.node_id == edge.target_id), edge.target_id))
            + (" (agent inference)" if edge.attribution == "agent_inference" else "")
            + " <span class=\"sources\">" + evidence_links(edge.evidence) + "</span></li>"
            for edge in diagram.edges
        ) + "</ol></article>"
        for index, diagram in enumerate(report.diagrams, start = 1)
    )
    report_equations = "".join(
        "<article><h3>" + escape(equation.title) + "</h3><pre>" + escape(equation.latex)
        + "</pre><p>" + escape(equation.explanation)
        + (" <strong>(agent inference)</strong>" if equation.attribution == "agent_inference" else "")
        + "</p><p class=\"sources\">Evidence: " + evidence_links(equation.evidence) + "</p></article>"
        for equation in report.equations
    )
    review_notes = "".join("<li>" + escape(item) + "</li>" for item in report.review_notes)
    contents = "".join(
        f'<li><a href="#chapter-{index}">{escape(chapter.title)}</a></li>'
        for index, chapter in enumerate(report.chapters, start = 1)
    )
    review = "This guide is a draft and requires human review. Source links open the extracted page."
    if not report.review_required:
        review = "Source coverage checks passed. This guide still needs evaluation for learning outcomes."
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(report.title)} — Learning Guide</title><style>"
        "body{font:17px/1.65 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#202124}"
        "h1,h2,h3,h4{line-height:1.25}section,article{margin:2rem 0}article{border-left:3px solid #8b8b8b;padding-left:1rem}"
        ".sources,.notice{font-size:.9rem;color:#555}a{color:#154f88}li{margin:.6rem 0}"
        "svg{display:block;width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:.9rem}"
        "th,td{border:1px solid #aaa;padding:.35rem;text-align:left;vertical-align:top}"
        "@page{size:A4;margin:18mm}"
        "@media print{body{max-width:none;margin:0;font-size:11pt}a{color:inherit;text-decoration:none}"
        "h1,h2{break-after:avoid}figure,article{break-inside:avoid}section{break-before:auto}"
        ".report-actions{display:none}}"
        "</style></head><body>"
        f"<header><h1>{escape(report.title)}</h1><p class=\"notice\">{escape(review)}</p>"
        "<p class=\"report-actions\"><a href=\"report.pdf\">Download PDF</a></p>"
        f"<p>Pages covered: {len(report.covered_pages)} / {len(report.covered_pages) + len(report.uncovered_pages)}. "
        f"Run: {escape(report.distill_run_id)}</p></header><nav aria-label=\"Learning Guide contents\"><h2>Contents</h2><ol>"
        "<li><a href=\"#central-thesis\">Central thesis</a></li>"
        "<li><a href=\"#key-takeaways\">Key takeaways</a></li>"
        "<li><a href=\"#chapter-connections\">Connections across chapters</a></li>"
        f"{contents}<li><a href=\"#glossary\">Glossary</a></li><li><a href=\"#limitations\">Limitations</a></li>"
        "</ol></nav><main>"
        f"<section id=\"central-thesis\"><h2>Central thesis</h2><p>{'<strong>Agent inference:</strong> ' if report.central_thesis_attribution == 'agent_inference' else ''}"
        f"{escape(report.central_thesis)}</p>"
        f"<p class=\"sources\">Sources: {source_links(report.central_thesis_source_refs)}</p></section>"
        f"<section id=\"key-takeaways\"><h2>Key takeaways</h2><ul>{takeaways}</ul></section>"
        f"<section id=\"chapter-connections\"><h2>Connections across chapters</h2><ul>{connections}</ul></section>"
        f"{''.join(chapters)}"
        + (f"<section><h2>Whole-book diagrams</h2>{report_diagrams}</section>" if report_diagrams else "")
        + (f"<section><h2>Whole-book equations</h2>{report_equations}</section>" if report_equations else "")
        + f"<section id=\"glossary\"><h2>Glossary</h2><dl>{glossary}</dl></section>"
        + f"<section id=\"limitations\"><h2>Limitations</h2><ul>{limitations}</ul>"
        + f"<h3>Review notes</h3><ul>{review_notes}</ul></section>"
        + "</main></body></html>"
    )
