"""Pure page-number mapping and evidence-candidate construction for the processed book."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


_HEADING = re.compile(r"^\s{0,3}(#{1,8})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_WORD = re.compile(r"[^a-z0-9]+")


def heading_evidence(markdown: str, section_path: list[str]) -> list[str]:
    headings = [match.group(2).strip() for match in _HEADING.finditer(markdown)]
    return list(dict.fromkeys([*section_path, *headings]))[:16]


def _normalized(value: str) -> str:
    return _WORD.sub(" ", value.casefold()).strip()


def _similarity(title: str, evidence: list[str]) -> float:
    left = _normalized(title)
    if not left:
        return 0.0
    best = 0.0
    for item in evidence:
        right = _normalized(item)
        if not right:
            continue
        if left == right or left in right or right in left:
            best = max(best, 1.0)
        else:
            best = max(best, SequenceMatcher(None, left, right).ratio())
    return best


def contents_candidates(outline: list[dict[str, Any]], page_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restrict each outline entry to a small, evidence-ranked set of selected source pages."""
    valid_pages = [
        row for row in page_evidence
        if type(row.get("source_page_number")) is int and isinstance(row.get("evidence"), list)
    ]
    if not valid_pages:
        return []
    result = []
    for index, item in enumerate(outline):
        title = item.get("title")
        source_page = item.get("page")
        if not isinstance(title, str) or not title.strip() or type(source_page) is not int or source_page < 1:
            continue
        scored = [(_similarity(title, row["evidence"]), row) for row in valid_pages]
        ranked = sorted(
            scored,
            key = lambda pair: (
                -pair[0],
                abs(pair[1]["source_page_number"] - source_page),
                pair[1]["source_page_number"],
            ),
        )
        matching = [row for score, row in ranked if score >= 0.28]
        chosen = matching[:5] if matching else ranked[:3]
        result.append({
            "outline_index": index,
            "title": title.strip()[:300],
            "level": max(1, min(8, item.get("level", 1) if type(item.get("level")) is int else 1)),
            "original_source_page_number": source_page,
            "candidates": [
                {
                    "source_page_number": row["source_page_number"],
                    "evidence": row["evidence"],
                }
                for _, row in chosen
            ],
        })
    return result


def remap_contents(entries: list[dict[str, Any]], pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Map source-based contents entries to dense processed page numbers without inventing pages."""
    if not entries or not pages:
        return [], "needs_review"
    page_by_source = {page["source_page_number"]: page for page in pages}
    source_numbers = sorted(page_by_source)
    remapped = []
    had_review = False
    for entry in entries:
        source_page = entry.get("source_page_number")
        if type(source_page) is not int or source_page < 1:
            had_review = True
            continue
        target = page_by_source.get(source_page)
        review_required = bool(entry.get("review_required", False))
        confidence = entry.get("confidence", 0.0)
        if target is None:
            replacement = next((number for number in source_numbers if number >= source_page), source_numbers[-1])
            target = page_by_source[replacement]
            confidence = min(float(confidence) if type(confidence) in (int, float) else 0.0, 0.4)
            review_required = True
        remapped.append({
            "level": entry.get("level", 1),
            "title": entry.get("title", ""),
            "processed_page_number": target["processed_page_number"],
            "source_page_number": target["source_page_number"],
            "confidence": confidence,
            "review_required": review_required,
        })
        had_review = had_review or review_required
    return remapped, "needs_review" if had_review or not remapped else "complete"
