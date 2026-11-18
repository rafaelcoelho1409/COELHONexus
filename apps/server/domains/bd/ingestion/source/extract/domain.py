"""Pure helpers (no I/O) — which page slices are left to extract, and how a slice's output is kept."""
from __future__ import annotations
from . import entities, params, patterns


def pending_slices(n_pages: int, done_pages: set[int], size: int) -> list[tuple[int, int]]:
    """`(first, last)` page ranges (1-based, inclusive) still to extract, at most `size` pages each. A run of pages not yet done is
    cut into slices, so a book that stopped half way (cancelled, killed by a redeploy) continues where it was and does not start over."""
    slices: list[tuple[int, int]] = []
    page = 1
    while page <= n_pages:
        if page in done_pages:
            page += 1
            continue
        last = page
        while last + 1 <= n_pages and last + 1 not in done_pages and last + 1 - page < size:
            last += 1
        slices.append((page, last))
        page = last + 1
    return slices


def figure_name(page_no: int, index: int) -> str:
    return params.FIGURE_NAME_FORMAT.format(page_no = page_no, index = index)


def epub_figure_name(section_no: int, index: int) -> str:
    return params.EPUB_FIGURE_NAME_FORMAT.format(section_no = section_no, index = index)


def epub_sections(markdown: str) -> tuple[entities.PageExtract, ...]:
    """Split the converted EPUB Markdown at its highest-level headings, retaining heading order and all source text."""
    lines = markdown.splitlines()
    headings = _epub_headings(lines)
    if not headings:
        return (_epub_unit(1, markdown.strip(), ()),)

    top_level = _section_heading_level(headings)
    starts = [heading for heading in headings if heading[1] == top_level]
    units: list[entities.PageExtract] = []
    for index, (start, _level, _title) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        unit_headings = tuple(
            (level, text)
            for line_no, level, text in headings
            if start <= line_no < end or (index == 0 and line_no < start)
        )
        units.append(_epub_unit(index + 1, "\n".join(lines[:end] if index == 0 else lines[start:end]).strip(), unit_headings))
    return tuple(units)


def epub_section_level(markdown: str) -> int | None:
    """The repeated Markdown heading level used to split one EPUB into reader sections, if any."""
    headings = _epub_headings(markdown.splitlines())
    return _section_heading_level(headings) if headings else None


def _epub_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    fence_marker = ""
    for line_no, line in enumerate(lines):
        fence = patterns.CODE_FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker[0]
            elif marker[0] == fence_marker:
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        heading = patterns.MARKDOWN_HEADING_RE.match(line)
        if heading:
            headings.append((line_no, len(heading.group(1)), heading.group(2).strip()))
    return headings


def _section_heading_level(headings: list[tuple[int, int, str]]) -> int:
    counts: dict[int, int] = {}
    for _line_no, level, _text in headings:
        counts[level] = counts.get(level, 0) + 1
    repeated_levels = [level for level, count in counts.items() if count > 1]
    return min(repeated_levels or counts)


def _epub_unit(page_no: int, markdown: str, headings: tuple[tuple[int, str], ...]) -> entities.PageExtract:
    return entities.PageExtract(
        page_no = page_no,
        markdown = markdown,
        headings = tuple(entities.Heading(level = level, text = text) for level, text in headings),
        tables = 0,
        pictures = 0,
        formulas = 0,
        figures = (),
    )


def strip_images(structured: dict) -> dict:
    """The structured document without the pictures' pixels: Docling embeds each picture as a base64 data URI, which would make
    the JSON many times the size of the text it describes. Pictures are kept as figure files; this leaves their place and bbox."""
    for picture in structured.get("pictures") or []:
        picture["image"] = None
    return structured
