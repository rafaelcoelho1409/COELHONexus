"""Pure helpers (no I/O) — a book outline as a clean tree, and where a reader unit sits in it."""
from __future__ import annotations
from . import entities, params


def entries_from_rows(rows: list[tuple[int, str, int | None]]) -> list[entities.OutlineEntry]:
    """Entries from `(level, title, page_index)` rows as the PDF reader gives them: levels count from 0 and page indexes from 0.
    A bookmark that points nowhere has no page and is left out."""
    return [
        entities.OutlineEntry(level = level + 1, title = title or "", page = page_index + 1)
        for level, title, page_index in rows
        if page_index is not None
    ]


def normalize(entries: list[entities.OutlineEntry], n_pages: int) -> list[entities.OutlineEntry]:
    """The outline a book can be trusted to have: no untitled or out-of-range entries, whitespace collapsed, and levels that start
    at 1 and never go more than one deeper than the entry before (a PDF that jumps from a chapter to a sub-sub-section gets the
    sub-section level). Document order is kept."""
    out: list[entities.OutlineEntry] = []
    previous_level = 0
    for entry in entries:
        title = " ".join(entry.title.split())[: params.TITLE_MAX_CHARS]
        if not title or not 1 <= entry.page <= n_pages:
            continue
        level = max(1, min(entry.level, previous_level + 1))
        candidate = entities.OutlineEntry(level = level, title = title, page = entry.page)
        if out and out[-1] == candidate:
            continue
        out.append(candidate)
        previous_level = level
    return out


def section_path(entries: list[entities.OutlineEntry], page: int) -> list[str]:
    """The titles from the top-level section down to the deepest one in effect on `page`: the last entry at each level that starts
    on or before it."""
    stack: list[entities.OutlineEntry] = []
    for entry in entries:
        if entry.page > page:
            continue
        while stack and stack[-1].level >= entry.level:
            stack.pop()
        stack.append(entry)
    return [entry.title for entry in stack]


def chapter_ranges(entries: list[entities.OutlineEntry], n_pages: int, level: int = 1) -> list[dict]:
    """The sections at `level` as page ranges: each runs to the page before the next one at that level or above, the last to the
    end of the book. Pages before the first section belong to none."""
    starts = [(i, entry) for i, entry in enumerate(entries) if entry.level <= level]
    ranges: list[dict] = []
    for position, (_, entry) in enumerate(starts):
        if entry.level != level:
            continue
        end = starts[position + 1][1].page - 1 if position + 1 < len(starts) else n_pages
        ranges.append({"title": entry.title, "first_page": entry.page, "last_page": max(entry.page, end)})
    return ranges


def to_dicts(entries: list[entities.OutlineEntry] | tuple[entities.OutlineEntry, ...]) -> list[dict]:
    return [{"level": entry.level, "title": entry.title, "page": entry.page} for entry in entries]


def from_dicts(rows: list[dict]) -> list[entities.OutlineEntry]:
    return [entities.OutlineEntry(level = int(r["level"]), title = str(r["title"]), page = int(r["page"])) for r in rows]


def from_content_units(units) -> list[entities.OutlineEntry]:
    """Build the section tree from heading metadata attached to ordered extracted units."""
    n_units = len(units)
    entries = [
        entities.OutlineEntry(level = heading.level, title = heading.text, page = unit.page_no)
        for unit in units
        for heading in unit.headings
    ]
    return normalize(entries, n_units)
