"""Plain scans of an extracted page that find places which may be defects, so the auditor judges leads instead of searching for them.

A scan decides nothing. Its evidence is a slice of the page by construction, so a lead can never be invented; the auditor says whether it
is a defect or how the source meant it. Code inside fences is content and is never scanned."""
from __future__ import annotations
from . import params

import re
from typing import Any


FENCE_RE = re.compile(r"(`{3,}|~{3,})")
HEADING_WITHOUT_SPACE_RE = re.compile(r"#{1,6}[A-Za-z]")
EMPTY_HEADING_RE = re.compile(r"#{1,6}\s*$")
# UTF-8 text read as Latin-1/Windows-1252 (`Ã©`, `Â `, `â€™`), and the replacement character.
MOJIBAKE_RE = re.compile("[ÃÂ][\u0080-¿]|â€.|�")


def prose_lines(markdown: str) -> list[tuple[int, str]]:
    """(offset, line without its newline) for every line outside a fenced code block, fence lines included in neither."""
    lines: list[tuple[int, str]] = []
    offset = 0
    opening: tuple[str, int] | None = None
    for raw in markdown.splitlines(keepends = True):
        line = raw.rstrip("\r\n")
        stripped = line.lstrip(" \t")
        fence = FENCE_RE.match(stripped)
        if opening is None and fence:
            opening = (fence.group(1)[0], len(fence.group(1)))
        elif opening is not None:
            marker, length = opening
            if re.fullmatch(rf"{re.escape(marker)}{{{length},}}\s*", stripped):
                opening = None
        else:
            lines.append((offset, line))
        offset += len(raw)
    return lines


def prose_blocks(markdown: str) -> list[tuple[int, int]]:
    """(start, end) of every paragraph-like block outside fenced code: consecutive prose lines up to a blank line or a fence."""
    blocks: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    previous_end = -1
    for offset, line in prose_lines(markdown):
        contiguous = current is not None and offset == previous_end + 1
        if not line.strip() or (current is not None and not contiguous):
            if current is not None:
                blocks.append(current)
                current = None
        if line.strip():
            current = (current[0] if current is not None else offset, offset + len(line))
        previous_end = offset + len(line)
    if current is not None:
        blocks.append(current)
    return blocks


def repeated_blocks(markdown: str) -> list[dict[str, Any]]:
    """A block of prose that appears more than once: its first copy as `evidence`, the second as `other_evidence`."""
    first: dict[str, tuple[int, int]] = {}
    seen_again: dict[str, tuple[int, int]] = {}
    counts: dict[str, int] = {}
    for start, end in prose_blocks(markdown):
        text = markdown[start:end]
        key = " ".join(text.split())
        if len(key) < params.MIN_REPEAT_CHARS or (key.startswith("#") and "\n" not in text):
            continue
        counts[key] = counts.get(key, 0) + 1
        if key not in first:
            first[key] = (start, end)
        elif key not in seen_again:
            seen_again[key] = (start, end)
    return [
        {
            "kind": "repeated_block",
            "evidence": markdown[first[key][0]:first[key][1]],
            "other_evidence": markdown[seen_again[key][0]:seen_again[key][1]],
            "note": f"the same block appears {count} times",
        }
        for key, count in counts.items()
        if count > 1
    ]


def heading_markers(markdown: str) -> list[dict[str, Any]]:
    """A heading marker with no space after it (`##Install`) and a heading with no text."""
    leads = []
    for _offset, line in prose_lines(markdown):
        if HEADING_WITHOUT_SPACE_RE.match(line):
            leads.append({"kind": "heading_without_space", "evidence": line, "note": "a `#` marker directly followed by text"})
        elif EMPTY_HEADING_RE.fullmatch(line):
            leads.append({"kind": "empty_heading", "evidence": line, "note": "a heading marker with no text"})
    return leads


def damaged_characters(markdown: str) -> list[dict[str, Any]]:
    """Text that looks like UTF-8 read as another encoding, or has the replacement character: the matching stretch of its line."""
    leads = []
    for offset, line in prose_lines(markdown):
        match = MOJIBAKE_RE.search(line)
        if match is None:
            continue
        start = max(0, match.start() - params.EVIDENCE_WINDOW // 2)
        leads.append({
            "kind": "damaged_characters",
            "evidence": line[start:start + params.EVIDENCE_WINDOW],
            "note": f"{match.group(0)!r} looks like text decoded with the wrong encoding",
        })
    return leads


def candidates(markdown: str) -> list[dict[str, Any]]:
    """Every lead on the page, first in reading order, at most `params.MAX_CANDIDATES`."""
    leads = [*heading_markers(markdown), *damaged_characters(markdown), *repeated_blocks(markdown)]
    return [
        {"candidate_id": f"c{number}", **lead}
        for number, lead in enumerate(
            sorted(leads, key = lambda lead: markdown.find(lead["evidence"]))[:params.MAX_CANDIDATES],
            start = 1,
        )
    ]
