"""Deterministic Markdown serializers for validated figure transcriptions."""
from __future__ import annotations
from . import params, patterns
from .... import agents

import json


def figure_context(markdown: str, marker_start: int, *, limit: int) -> str:
    """Return a bounded, image-marker-free excerpt around the figure's page position."""
    start = max(0, marker_start - limit // 2)
    excerpt = markdown[start:start + limit]
    excerpt = patterns.IMAGE_COMMENT_RE.sub("[figure]", excerpt)
    return excerpt.strip()


def render_conversion(result: agents.schemas.VisualExtractionResult) -> tuple[str | None, str]:
    """Render only validated, high-confidence data; uncertainty leaves the original figure in place."""
    if result.decision != "convert" or result.kind is None:
        return None, result.rationale
    threshold = (
        params.MIN_NUMERIC_CONFIDENCE
        if result.kind in {
            agents.schemas.VisualKind.CHART,
            agents.schemas.VisualKind.EQUATION,
            agents.schemas.VisualKind.MAP,
        }
        else params.MIN_CONFIDENCE
    )
    if result.confidence < threshold:
        return None, f"confidence {result.confidence:.2f} is below the {threshold:.2f} conversion threshold"

    match result.kind:
        case agents.schemas.VisualKind.DIAGRAM:
            content = _diagram(result)
        case agents.schemas.VisualKind.TABLE:
            content = _table(result)
        case agents.schemas.VisualKind.CHART:
            content = _chart(result)
        case agents.schemas.VisualKind.EQUATION:
            content = _equation(result)
        case agents.schemas.VisualKind.CODE:
            content = _code(result)
        case agents.schemas.VisualKind.TEXT:
            content = _text(result)
        case agents.schemas.VisualKind.TIMELINE:
            content = _timeline(result)
        case agents.schemas.VisualKind.MAP:
            content = _map(result)
        case _:
            return None, "unsupported visual kind"
    if not content or len(content) > params.MAX_MARKDOWN_CHARS:
        return None, "the structured transcription exceeds safe output bounds"
    return content, "converted from the source figure"


def _diagram(result: agents.schemas.VisualExtractionResult) -> str | None:
    if len(result.diagram_nodes) > params.MAX_DIAGRAM_NODES or len(result.diagram_edges) > params.MAX_DIAGRAM_EDGES:
        return None
    ids = {node.id: f"n{index}" for index, node in enumerate(result.diagram_nodes)}
    if len(ids) < 2 or any(edge.source not in ids or edge.target not in ids for edge in result.diagram_edges):
        return None
    lines = [f"```mermaid\nflowchart {result.diagram_direction}"]
    lines.extend(f'{ids[node.id]}["{_mermaid_text(node.label)}"]' for node in result.diagram_nodes)
    for edge in result.diagram_edges:
        link = f"{ids[edge.source]} --> {ids[edge.target]}"
        lines.append(
            f"{ids[edge.source]} -->|{_mermaid_text(edge.label)}| {ids[edge.target]}"
            if edge.label else link
        )
    return "\n".join([*lines, "```"])


def _table(result: agents.schemas.VisualExtractionResult) -> str | None:
    headers = result.table_headers
    rows = result.table_rows
    if not headers or len(headers) > params.MAX_TABLE_COLUMNS or not rows or len(rows) > params.MAX_TABLE_ROWS:
        return None
    if any(len(row) != len(headers) for row in rows):
        return None
    header = "| " + " | ".join(_table_cell(cell) for cell in headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_table_cell(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def _chart(result: agents.schemas.VisualExtractionResult) -> str | None:
    rows = _chart_rows(result)
    if not rows or len(rows) > params.MAX_CHART_ROWS:
        return None
    spec = _chart_spec(result, rows)
    heading = f"**{_markdown_literal(result.chart_title or 'Chart transcribed from source figure')}**"
    axes = []
    if result.chart_x_label:
        axes.append(f"x-axis: {_markdown_literal(result.chart_x_label)}")
    if result.chart_y_label:
        axes.append(f"y-axis: {_markdown_literal(result.chart_y_label)}")
    table = _chart_data_table(result, rows)
    sections = [heading]
    if axes:
        sections.append(" · ".join(axes))
    sections.extend([table, _fenced_json(spec, "vega-lite")])
    return "\n\n".join(sections)


def _chart_rows(result: agents.schemas.VisualExtractionResult) -> list[dict[str, object]]:
    if result.chart_rows:
        rows = []
        for index, point in enumerate(result.chart_rows):
            row: dict[str, object] = {"x": point.x}
            if point.y is not None:
                row["y"] = point.y
            if point.value is not None:
                row["value"] = point.value
            if point.series:
                row["series"] = point.series
            row["order"] = index
            rows.append(row)
        return rows
    return [
        {"x": label, "value": value, "order": index}
        for index, (label, value) in enumerate(zip(result.chart_categories, result.chart_values, strict = True))
    ]


def _chart_spec(result: agents.schemas.VisualExtractionResult, rows: list[dict[str, object]]) -> dict[str, object]:
    mark = {
        "bar": "bar",
        "line": "line",
        "area": "area",
        "scatter": "point",
        "pie": "arc",
        "heatmap": "rect",
    }[result.chart_type]
    series = any("series" in row for row in rows)
    encoding: dict[str, object]
    if result.chart_type == "scatter":
        encoding = {
            "x": {"field": "x", "type": "quantitative", "title": result.chart_x_label or None},
            "y": {"field": "y", "type": "quantitative", "title": result.chart_y_label or None},
        }
    elif result.chart_type == "heatmap":
        x_order = list(dict.fromkeys(row["x"] for row in rows))
        y_order = list(dict.fromkeys(row["y"] for row in rows))
        encoding = {
            "x": {"field": "x", "type": "nominal", "title": result.chart_x_label or None, "sort": x_order},
            "y": {"field": "y", "type": "nominal", "title": result.chart_y_label or None, "sort": y_order},
            "color": {"field": "value", "type": "quantitative", "legend": {"title": "Value"}},
        }
    elif result.chart_type == "pie":
        x_order = list(dict.fromkeys(row["x"] for row in rows))
        encoding = {
            "theta": {"field": "value", "type": "quantitative"},
            "color": {"field": "x", "type": "nominal", "title": result.chart_x_label or None, "sort": x_order},
        }
    else:
        x_type = "quantitative" if all(isinstance(row["x"], (int, float)) for row in rows) else "nominal"
        encoding = {
            "x": {
                "field": "x",
                "type": x_type,
                "title": result.chart_x_label or None,
                **({} if x_type == "quantitative" else {"sort": list(dict.fromkeys(row["x"] for row in rows))}),
            },
            "y": {"field": "value", "type": "quantitative", "title": result.chart_y_label or None},
        }
        if result.chart_type in {"line", "area"}:
            encoding["order"] = {"field": "order", "type": "quantitative"}
        if result.chart_type == "area":
            encoding["y"] = {**encoding["y"], "stack": None}
    if series and result.chart_type != "heatmap":
        encoding["color"] = {"field": "series", "type": "nominal", "title": "Series"}
        if result.chart_type == "bar":
            encoding["xOffset"] = {"field": "series", "type": "nominal"}
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "title": result.chart_title or None,
        "description": "Transcribed chart data; values are taken from the source figure.",
        "data": {"values": rows},
        "mark": mark,
        "encoding": encoding,
        "width": 640,
        "height": 360,
    }


def _chart_data_table(result: agents.schemas.VisualExtractionResult, rows: list[dict[str, object]]) -> str:
    headers = [result.chart_x_label or "x"]
    if result.chart_type in {"scatter", "heatmap"}:
        headers.append(result.chart_y_label or "y")
    if result.chart_type != "scatter":
        headers.append("Value")
    if any("series" in row for row in rows):
        headers.append("Series")
    table_rows = []
    for row in rows:
        values = [row.get("x", "")]
        if result.chart_type in {"scatter", "heatmap"}:
            values.append(row.get("y", ""))
        if result.chart_type != "scatter":
            values.append(row.get("value", ""))
        if "Series" in headers:
            values.append(row.get("series", ""))
        table_rows.append(values)
    header = "| " + " | ".join(_table_cell(value) for value in headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_table_cell(str(value)) for value in row) + " |" for row in table_rows]
    return "\n".join([header, divider, *body])


def _map(result: agents.schemas.VisualExtractionResult) -> str | None:
    coordinate_count = sum(len(feature.coordinates) for feature in result.map_features)
    if not result.map_features or coordinate_count > params.MAX_MAP_COORDINATES:
        return None
    features = []
    readable_rows = []
    for feature in result.map_features:
        coordinates = [[point.longitude, point.latitude] for point in feature.coordinates]
        if feature.geometry_type == "Point":
            geometry_coordinates: object = coordinates[0]
        elif feature.geometry_type == "LineString":
            geometry_coordinates = coordinates
        else:
            geometry_coordinates = [coordinates]
        features.append({
            "type": "Feature",
            "properties": {"label": feature.label, "category": feature.category},
            "geometry": {"type": feature.geometry_type, "coordinates": geometry_coordinates},
        })
        readable_rows.append([
            feature.label,
            feature.geometry_type,
            "; ".join(f"{point.longitude:g}, {point.latitude:g}" for point in feature.coordinates),
            feature.category,
        ])
    encoding: dict[str, object] = {
        "shape": {"field": "geometry", "type": "geojson"},
    }
    if any(feature.category for feature in result.map_features):
        encoding["color"] = {"field": "properties.category", "type": "nominal"}
    if any(feature.label for feature in result.map_features):
        encoding["tooltip"] = [
            {"field": "properties.label", "type": "nominal", "title": "Feature"},
            {"field": "properties.category", "type": "nominal", "title": "Category"},
        ]
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "title": result.map_title or None,
        "description": "Map geometries transcribed from explicit source coordinates.",
        "data": {"values": features},
        "projection": {"type": "equalEarth"},
        "mark": {"type": "geoshape", "stroke": "#ffffff", "strokeWidth": 1},
        "encoding": encoding,
        "width": 640,
        "height": 360,
    }
    table = "\n".join([
        "| Feature | Geometry | Explicit coordinates (longitude, latitude) | Category |",
        "| --- | --- | --- | --- |",
        *["| " + " | ".join(_table_cell(value) for value in row) + " |" for row in readable_rows],
    ])
    heading = f"**{_markdown_literal(result.map_title or 'Map transcribed from source figure')}**"
    return "\n\n".join([heading, table, _fenced_json(spec, "vega-lite")])


def _fenced_json(value: dict[str, object], language: str) -> str:
    return f"```{language}\n{json.dumps(value, ensure_ascii = False, separators = (',', ':'))}\n```"


def _equation(result: agents.schemas.VisualExtractionResult) -> str | None:
    latex = result.equation_latex.strip()
    if not latex or patterns.BLOCKED_TEX_RE.search(latex) or "\x00" in latex:
        return None
    return f"$$\n{latex}\n$$"


def _code(result: agents.schemas.VisualExtractionResult) -> str | None:
    code = result.code_text.rstrip()
    language = result.code_language.strip()
    if not code or not patterns.LANGUAGE_RE.fullmatch(language):
        return None
    return _fenced_code(code, language)


def _text(result: agents.schemas.VisualExtractionResult) -> str | None:
    text = result.transcribed_text.strip()
    if not text:
        return None
    if result.text_format == "code":
        language = result.code_language.strip()
        if not patterns.LANGUAGE_RE.fullmatch(language):
            return None
        return _fenced_code(text, language)

    title = result.text_title.strip() or "Text transcribed from source figure"
    lines = [f"> **{_markdown_literal(title)}**", ">"]
    lines.extend(f"> {_markdown_literal(line)}" if line else ">" for line in text.splitlines())
    return "\n".join(lines)


def _fenced_code(code: str, language: str) -> str:
    fence_length = max(
        3,
        max((len(match.group(0)) for match in patterns.BACKTICK_RUN_RE.finditer(code)), default = 0) + 1,
    )
    fence = "`" * fence_length
    return f"{fence}{language}\n{code}\n{fence}"


def _timeline(result: agents.schemas.VisualExtractionResult) -> str | None:
    events = result.timeline_events
    if len(events) < 2:
        return None
    lines = ["```mermaid", "timeline"]
    if result.timeline_title:
        lines.append(f"    title {_mermaid_text(result.timeline_title)}")
    for event in events:
        period = _mermaid_text(event.period).replace(":", "#58;")
        label = _mermaid_text(event.label)
        lines.append(f"    {period} : {label}")
    return "\n".join([*lines, "```"])


def _markdown_literal(value: str) -> str:
    return patterns.MARKDOWN_ESCAPE_RE.sub(r"\\\1", value).replace("&", "&amp;")


def _table_cell(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
    )


def _mermaid_text(value: str) -> str:
    text = " ".join(value.split())
    return (
        text.replace("#", "#35;")
        .replace("&", "#amp;")
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
        .replace("(", "#40;")
        .replace(")", "#41;")
        .replace("|", "#124;")
    )


