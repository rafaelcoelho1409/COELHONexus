"""Deterministic, script-free HTML/SVG rendering for validated Distill reports."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from .. import schemas


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


def _geojson_positions(value: Any) -> list[tuple[float, float]]:
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            return [(float(value[0]), float(value[1]))]
        return [position for item in value for position in _geojson_positions(item)]
    return []


def _map_svg(map_spec: schemas.GeoMapSpec, identifier: str) -> str:
    features = map_spec.geojson.features
    positions = [
        position
        for feature in features
        for position in _geojson_positions(feature.geometry.coordinates)
    ]
    if not positions:
        return ""

    width, height, padding = 760, 420, 28
    min_longitude = min(longitude for longitude, _ in positions)
    max_longitude = max(longitude for longitude, _ in positions)
    min_latitude = min(latitude for _, latitude in positions)
    max_latitude = max(latitude for _, latitude in positions)
    longitude_span = max(max_longitude - min_longitude, 1e-6)
    latitude_span = max(max_latitude - min_latitude, 1e-6)
    scale = min((width - 2 * padding) / longitude_span, (height - 2 * padding) / latitude_span)
    offset_x = (width - longitude_span * scale) / 2
    offset_y = (height - latitude_span * scale) / 2

    def project(position: tuple[float, float]) -> tuple[float, float]:
        longitude, latitude = position
        return (
            offset_x + (longitude - min_longitude) * scale,
            offset_y + (max_latitude - latitude) * scale,
        )

    def line_path(coordinates: list[tuple[float, float]], close: bool = False) -> str:
        projected = [project(position) for position in coordinates]
        if not projected:
            return ""
        commands = [f"M {_svg_number(projected[0][0])} {_svg_number(projected[0][1])}"]
        commands.extend(f"L {_svg_number(x)} {_svg_number(y)}" for x, y in projected[1:])
        if close:
            commands.append("Z")
        return " ".join(commands)

    colors = ("#245b91", "#9c4c16", "#32734d", "#7c4b8e", "#8b3447")
    parts = [
        f'<svg class="report-map" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="{identifier}-title {identifier}-description">',
        f'<title id="{identifier}-title">{html.escape(map_spec.title)}</title>',
        f'<desc id="{identifier}-description">{html.escape(map_spec.accessible_description)}</desc>',
        f'<rect x="{padding}" y="{padding}" width="{width - 2 * padding}" '
        f'height="{height - 2 * padding}" fill="none" stroke="#9ca3af" />',
    ]
    for feature_index, feature in enumerate(features):
        color = colors[feature_index % len(colors)]
        geometry = feature.geometry
        kind = geometry.type
        coordinates = geometry.coordinates
        if kind == "Point":
            x, y = project(coordinates)
            parts.append(
                f'<circle cx="{_svg_number(x)}" cy="{_svg_number(y)}" r="7" fill="{color}">'
                f'<title>{html.escape(feature.properties.label)} · {coordinates[0]}, {coordinates[1]}</title></circle>'
            )
        elif kind == "MultiPoint":
            for longitude, latitude in coordinates:
                x, y = project((longitude, latitude))
                parts.append(
                    f'<circle cx="{_svg_number(x)}" cy="{_svg_number(y)}" r="6" fill="{color}">'
                    f'<title>{html.escape(feature.properties.label)} · {longitude}, {latitude}</title></circle>'
                )
        elif kind == "LineString":
            parts.append(
                f'<path d="{line_path(coordinates)}" fill="none" stroke="{color}" '
                f'stroke-width="3"><title>{html.escape(feature.properties.label)}</title></path>'
            )
        elif kind == "MultiLineString":
            for line in coordinates:
                parts.append(
                    f'<path d="{line_path(line)}" fill="none" stroke="{color}" '
                    f'stroke-width="3"><title>{html.escape(feature.properties.label)}</title></path>'
                )
        elif kind == "Polygon":
            path = " ".join(line_path(ring, close = True) for ring in coordinates)
            parts.append(
                f'<path d="{path}" fill="{color}" fill-opacity="0.2" stroke="{color}" '
                f'stroke-width="2" fill-rule="evenodd"><title>{html.escape(feature.properties.label)}</title></path>'
            )
        else:
            for polygon in coordinates:
                path = " ".join(line_path(ring, close = True) for ring in polygon)
                parts.append(
                    f'<path d="{path}" fill="{color}" fill-opacity="0.2" stroke="{color}" '
                    f'stroke-width="2" fill-rule="evenodd"><title>{html.escape(feature.properties.label)}</title></path>'
                )
    parts.append(
        f'<text x="{width / 2}" y="{height - 6}" text-anchor="middle" font-size="12">'
        'Longitude · source-coordinate schematic</text></svg>'
    )
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

    def map_html(map_spec: schemas.GeoMapSpec, identifier: str) -> str:
        features = "".join(
            "<li><strong>" + escape(feature.properties.label) + "</strong>"
            + (" — " + escape(feature.properties.description) if feature.properties.description else "")
            + (" <em>(agent inference)</em>" if feature.properties.attribution == "agent_inference" else "")
            + " <span class=\"sources\">" + evidence_links(feature.properties.evidence) + "</span>"
            + "<details><summary>Evidence</summary><ul>"
            + "".join(
                "<li>" + escape(quote.quote) + " — " + evidence_links([quote]) + "</li>"
                for quote in feature.properties.evidence
            )
            + "</ul></details></li>"
            for feature in map_spec.geojson.features
        )
        return (
            "<article class=\"report-map-container\"><h3>" + escape(map_spec.title) + "</h3><p>"
            + escape(map_spec.accessible_description)
            + "</p><p class=\"sources\">Schematic map drawn from source coordinates; no basemap or inferred locations.</p>"
            + _map_svg(map_spec, identifier) + "<ul>" + features + "</ul></article>"
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
        timelines = "".join(
            "<article class=\"timeline\"><h3>" + escape(timeline.title) + "</h3><p>"
            + escape(timeline.accessible_description) + "</p><ol>"
            + "".join(
                "<li><strong>" + escape(event.date_or_order) + " · " + escape(event.label) + "</strong>: "
                + escape(event.description)
                + (" <em>(agent inference)</em>" if event.attribution == "agent_inference" else "")
                + " <span class=\"sources\">" + source_links(event.source_refs) + "</span></li>"
                for event in timeline.events
            ) + "</ol></article>"
            for timeline in chapter.timelines
        )
        maps = "".join(
            map_html(map_spec, f"map-{index}-{map_index}")
            for map_index, map_spec in enumerate(chapter.maps, start = 1)
        )
        chapters.append(
            f'<section id="chapter-{index}"><h2>{escape(chapter.title)}</h2>'
            f'<p>{"<strong>Agent inference:</strong> " if chapter.overview_attribution == "agent_inference" else ""}'
            f'{escape(chapter.overview).replace(chr(10), "<br>")}</p>'
            f'<p class="sources">Sources: {source_links(chapter.overview_source_refs)}</p>{lessons}'
            f'<h3>Recall questions</h3><ol>{questions}</ol>{diagrams}{equations}{charts}{timelines}{maps}</section>'
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
    report_maps = "".join(
        map_html(map_spec, f"book-map-{index}")
        for index, map_spec in enumerate(report.maps, start = 1)
    )
    review_notes = "".join("<li>" + escape(item) + "</li>" for item in report.review_notes)
    decision_brief_html = ""
    if report.decision_brief is not None:
        decision = report.decision_brief
        options = "".join(
            "<article><h3>" + escape(option.title) + "</h3><p>" + escape(option.expected_outcome)
            + (" <em>(agent inference)</em>" if option.attribution == "agent_inference" else "")
            + "</p><p>Trade-offs: " + escape("; ".join(option.tradeoffs) or "Not specified")
            + "</p><p>Risks: " + escape("; ".join(option.risks) or "Not specified")
            + "</p><p class=\"sources\">" + source_links(option.source_refs) + "</p></article>"
            for option in decision.options
        )
        actions = "".join(
            "<li><strong>" + escape(step.action) + "</strong> — " + escape(step.rationale)
            + " <span>Success measure: " + escape(step.success_measure) + "</span>"
            + (" <em>(agent inference)</em>" if step.attribution == "agent_inference" else "")
            + " <span class=\"sources\">" + source_links(step.source_refs) + "</span></li>"
            for step in decision.action_plan
        )
        decision_brief_html = (
            "<section id=\"decision-brief\"><h2>Decision brief</h2><p><strong>Decision question:</strong> "
            + escape(decision.decision_question) + "</p>" + lesson_html(decision.recommendation)
            + ("<h3>Options</h3>" + options if options else "")
            + ("<h3>Assumptions</h3><ul>" + "".join("<li>" + escape(item) + "</li>" for item in decision.assumptions) + "</ul>" if decision.assumptions else "")
            + ("<h3>Risks</h3><ul>" + "".join("<li>" + escape(item) + "</li>" for item in decision.risks) + "</ul>" if decision.risks else "")
            + ("<h3>Action plan</h3><ol>" + actions + "</ol>" if actions else "")
            + "</section>"
        )
    research = report.research_context
    research_items = "".join(
        "<li><article><h3><a href=\"" + escape(source.url) + "\">" + escape(source.title)
        + "</a></h3><p>" + escape(source.snippet) + "</p><p class=\"sources\">"
        + escape(source.publisher or source.provider_id)
        + (" · Published: " + escape(source.published_at) if source.published_at else "")
        + " · Accessed: " + datetime.fromtimestamp(source.accessed_at, timezone.utc).date().isoformat()
        + " · Query: " + escape(source.query) + "</p></article></li>"
        for source in research.sources
    )
    research_sources_by_id = {source.source_id: source for source in research.sources}
    research_findings = "".join(
        "<article><h3>" + escape(finding.title) + "</h3><p>" + escape(finding.summary)
        + "</p><p><strong>Relation to the book:</strong> " + escape(finding.relevance_to_book)
        + "</p><p class=\"sources\">External sources: "
        + "; ".join(
            "<a href=\"" + escape(research_sources_by_id[source_id].url) + "\">"
            + escape(research_sources_by_id[source_id].title) + "</a>"
            for source_id in finding.source_ids
            if source_id in research_sources_by_id
        )
        + "</p></article>"
        for finding in research.findings
    )
    research_context_html = ""
    if research.status != "disabled":
        if research.status == "unsupported_provider":
            research_note = "The selected search connection is not supported by this Distill MCP adapter."
        elif research.status == "unavailable":
            research_note = "External search was unavailable; the source-grounded guide is unaffected."
        elif research.status == "no_results":
            research_note = "External search returned no usable sources."
        else:
            research_note = "External references are for current context and are not evidence for book-derived claims."
        research_context_html = (
            "<section id=\"research-context\"><h2>Current context and further reading</h2><p>"
            + escape(research_note) + "</p>"
            + ("<h3>External context</h3>" + research_findings if research_findings else "")
            + ("<ul>" + research_items + "</ul>" if research_items else "")
            + "</section>"
        )
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
        + ("<li><a href=\"#decision-brief\">Decision brief</a></li>" if report.decision_brief is not None else "")
        + ("<li><a href=\"#research-context\">Current context and further reading</a></li>" if research.status != "disabled" else "")
        + "<li><a href=\"#key-takeaways\">Key takeaways</a></li>"
        "<li><a href=\"#chapter-connections\">Connections across chapters</a></li>"
        f"{contents}<li><a href=\"#glossary\">Glossary</a></li><li><a href=\"#limitations\">Limitations</a></li>"
        "</ol></nav><main>"
        f"<section id=\"central-thesis\"><h2>Central thesis</h2><p>{'<strong>Agent inference:</strong> ' if report.central_thesis_attribution == 'agent_inference' else ''}"
        f"{escape(report.central_thesis)}</p>"
        f"<p class=\"sources\">Sources: {source_links(report.central_thesis_source_refs)}</p></section>"
        + decision_brief_html
        + research_context_html
        + f"<section id=\"key-takeaways\"><h2>Key takeaways</h2><ul>{takeaways}</ul></section>"
        f"<section id=\"chapter-connections\"><h2>Connections across chapters</h2><ul>{connections}</ul></section>"
        f"{''.join(chapters)}"
        + (f"<section><h2>Whole-book diagrams</h2>{report_diagrams}</section>" if report_diagrams else "")
        + (f"<section><h2>Whole-book equations</h2>{report_equations}</section>" if report_equations else "")
        + (f"<section><h2>Whole-book maps</h2>{report_maps}</section>" if report_maps else "")
        + f"<section id=\"glossary\"><h2>Glossary</h2><dl>{glossary}</dl></section>"
        + f"<section id=\"limitations\"><h2>Limitations</h2><ul>{limitations}</ul>"
        + f"<h3>Review notes</h3><ul>{review_notes}</ul></section>"
        + "</main></body></html>"
    )
