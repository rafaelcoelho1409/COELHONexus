"""Pure heading discovery, contents construction, and processed page-number mapping."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


_HEADING = re.compile(r"^\s{0,3}(#{1,8})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_WORD = re.compile(r"[^a-z0-9]+")
_MAX_PAGE_HEADINGS = 64
_MAX_CONTENTS_ENTRIES = 20000
_NAVIGATION_HEADINGS = frozenset({
    "contents",
    "brief contents",
    "table of contents",
    "detailed contents",
    "contents in detail",
    "endnotes",
    "index",
})
_NON_CONTENT_ROLES = frozenset({"navigation", "legal", "noise", "preface", "reference"})


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
        if left == right:
            score = 1.0
        elif f" {left} " in f" {right} " or f" {right} " in f" {left} ":
            score = min(0.97, SequenceMatcher(None, left, right).ratio())
        else:
            score = SequenceMatcher(None, left, right).ratio()
        best = max(best, score)
    return best


def _is_main_structure_heading(title: str) -> bool:
    normalized = _normalized(title)
    return re.match(
        r"^(?:introduction|prologue|chapter\s+[ivxlcdm0-9]+|part\s+[ivxlcdm0-9]+|appendix\s+[a-z0-9]+)\b",
        normalized,
    ) is not None


def _is_noise_heading(title: str) -> bool:
    normalized = _normalized(title)
    return (
        not normalized
        or normalized in _NAVIGATION_HEADINGS
        or re.fullmatch(r"(?:\d+|[ivxlcdm]+)", normalized) is not None
        or re.fullmatch(r"(?:part|chapter)\s+[ivxlcdm0-9]+", normalized) is not None
        or normalized.startswith("praise for ")
        or normalized.startswith("about the author")
        or normalized.startswith("about the technical reviewer")
        or normalized.startswith("by ")
    )


def _first_main_structure_page(pages: list[Any]) -> int | None:
    for page in pages:
        if page.get("role") in {"navigation", "legal", "noise", "reference"}:
            continue
        section_path = page.get("section_path", [])
        headings = page.get("headings", [])
        titles = [value for value in section_path if isinstance(value, str)] if isinstance(section_path, list) else []
        if isinstance(headings, list):
            titles.extend(
                item["title"]
                for item in headings
                if isinstance(item, dict) and isinstance(item.get("title"), str)
            )
        if any(_is_main_structure_heading(title) for title in titles):
            return page["source_page_number"]
    return None


def markdown_headings(markdown: str) -> list[dict[str, Any]]:
    """Read explicit Markdown heading levels without treating body text as structure."""
    return [
        {"title": match.group(2).strip(), "level": len(match.group(1)), "order": index}
        for index, match in enumerate(_HEADING.finditer(markdown))
        if match.group(2).strip()
    ][:_MAX_PAGE_HEADINGS]


def heading_evidence(markdown: str, section_path: list[str]) -> list[str]:
    headings = [item["title"] for item in markdown_headings(markdown)]
    return list(dict.fromkeys([*section_path, *headings]))[:16]


def canonical_heading_candidates(page_evidence: list[Any]) -> list[dict[str, Any]]:
    """Collect actual page headings and section-path transitions from selected pages only."""
    valid_pages = sorted(
        (
            row for row in page_evidence
            if isinstance(row, dict)
            and type(row.get("source_page_number")) is int
            and row["source_page_number"] > 0
        ),
        key = lambda row: row["source_page_number"],
    )
    first_main_page = _first_main_structure_page(valid_pages)
    result: list[dict[str, Any]] = []
    previous_path: list[str] = []
    for page in valid_pages:
        if page.get("role") in _NON_CONTENT_ROLES:
            previous_path = []
            continue
        source_page_number = page["source_page_number"]
        if first_main_page is not None and source_page_number < first_main_page:
            previous_path = []
            continue
        section_path = [
            value.strip() for value in page.get("section_path", [])
            if isinstance(value, str) and value.strip()
        ] if isinstance(page.get("section_path"), list) else []
        shared_depth = 0
        while (
            shared_depth < min(len(previous_path), len(section_path))
            and _normalized(previous_path[shared_depth]) == _normalized(section_path[shared_depth])
        ):
            shared_depth += 1

        page_rows: list[dict[str, Any]] = []
        for path_index in range(shared_depth, len(section_path)):
            page_rows.append({
                "title": section_path[path_index][:300],
                "level": min(path_index + 1, 8),
                "order": path_index,
                "source_page_number": source_page_number,
                "confidence": 0.9,
                "provenance": "section_path",
            })
        section_path_titles = [_normalized(value) for value in section_path]
        raw_headings = page.get("headings", [])
        if isinstance(raw_headings, list):
            for item in raw_headings[:_MAX_PAGE_HEADINGS]:
                if not isinstance(item, dict):
                    continue
                title = item.get("title")
                level = item.get("level")
                order = item.get("order")
                if not isinstance(title, str) or not title.strip() or type(level) is not int:
                    continue
                title_key = _normalized(title)
                if (
                    _is_noise_heading(title)
                    or any(
                        title_key == path_title
                        or f" {title_key} " in f" {path_title} "
                        for path_title in section_path_titles
                    )
                ):
                    continue
                page_rows.append({
                    "title": title.strip()[:300],
                    "level": max(1, min(level, 8)),
                    "order": 1000 + (order if type(order) is int else 0),
                    "source_page_number": source_page_number,
                    "confidence": 0.96,
                    "provenance": "processed_page_heading",
                })

        seen_on_page: set[str] = set()
        for row in page_rows:
            title_key = _normalized(row["title"])
            if _is_noise_heading(row["title"]) or title_key in seen_on_page:
                continue
            seen_on_page.add(title_key)
            result.append(row)
            if len(result) >= _MAX_CONTENTS_ENTRIES:
                return result
        previous_path = section_path
    return result


def contents_candidates(outline: list[Any], page_evidence: list[Any]) -> list[dict[str, Any]]:
    """Rank retained pages as possible anchors for each source-outline entry."""
    valid_pages = []
    for row in page_evidence:
        if (
            not isinstance(row, dict)
            or type(row.get("source_page_number")) is not int
            or row["source_page_number"] < 1
            or not isinstance(row.get("evidence"), list)
            or row.get("role") in {"navigation", "legal", "noise"}
        ):
            continue
        valid_pages.append({
            "source_page_number": row["source_page_number"],
            "evidence": [value for value in row["evidence"] if isinstance(value, str)],
        })
    if not valid_pages:
        return []
    result = []
    for index, item in enumerate(outline):
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        source_page = item.get("page")
        if not isinstance(title, str) or not title.strip() or type(source_page) is not int or source_page < 1:
            continue
        scored = [(_similarity(title, row["evidence"]), row) for row in valid_pages]
        ranked = sorted(
            scored,
            key = lambda pair: (
                -pair[0],
                pair[1]["source_page_number"] < source_page,
                abs(pair[1]["source_page_number"] - source_page),
                pair[1]["source_page_number"],
            ),
        )
        matching = [(score, row) for score, row in ranked if score >= 0.28]
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
                    "similarity": score,
                }
                for score, row in chosen
            ],
        })
    return result


def resolve_unambiguous_contents(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve strong page matches in code and return only genuinely uncertain rows for the LLM."""
    resolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for entry in entries:
        candidates = entry.get("candidates", [])
        ranked = []
        for candidate in candidates:
            page_number = candidate.get("source_page_number")
            if type(page_number) is not int or page_number < 1:
                continue
            score = candidate.get("similarity")
            if type(score) not in (int, float) or score <= 0:
                score = _similarity(entry.get("title", ""), candidate.get("evidence", []))
            ranked.append((float(score), candidate))
        ranked.sort(
            key = lambda pair: (
                -pair[0],
                pair[1]["source_page_number"] < entry["original_source_page_number"],
                abs(pair[1]["source_page_number"] - entry["original_source_page_number"]),
                pair[1]["source_page_number"],
            ),
        )
        if not ranked:
            resolved.append({
                "outline_index": entry["outline_index"],
                "title": entry["title"],
                "level": entry["level"],
                "source_page_number": entry["original_source_page_number"],
                "confidence": 0.2,
                "review_required": True,
            })
            continue

        best_score, best = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        page_number = best["source_page_number"]
        source_anchor_agrees = page_number >= entry["original_source_page_number"] and best_score >= 0.98
        decisive_score = (
            len(_normalized(entry.get("title", "")).split()) >= 3
            and best_score >= 0.88
            and best_score - second_score >= 0.18
        )
        if best_score >= 0.98 or source_anchor_agrees or decisive_score:
            resolved.append({
                "outline_index": entry["outline_index"],
                "title": entry["title"],
                "level": entry["level"],
                "source_page_number": page_number,
                "confidence": min(0.99, max(0.75, best_score)),
                "review_required": best_score < 0.78,
            })
        elif best_score >= 0.4 and len(ranked) > 1:
            ambiguous.append(entry)
        else:
            resolved.append({
                "outline_index": entry["outline_index"],
                "title": entry["title"],
                "level": entry["level"],
                "source_page_number": page_number,
                "confidence": min(0.45, max(0.2, best_score)),
                "review_required": True,
            })
    return resolved, ambiguous


def fallback_contents_candidates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep uncertain source entries visible at the closest retained page and flag them for review."""
    result = []
    for entry in entries:
        candidates = []
        for candidate in entry.get("candidates", []):
            page_number = candidate.get("source_page_number")
            if type(page_number) is not int or page_number < 1:
                continue
            score = candidate.get("similarity")
            if type(score) not in (int, float) or score <= 0:
                score = _similarity(entry.get("title", ""), candidate.get("evidence", []))
            candidates.append((float(score), candidate))
        candidates.sort(key = lambda pair: (
            -pair[0],
            pair[1]["source_page_number"] < entry["original_source_page_number"],
            abs(pair[1]["source_page_number"] - entry["original_source_page_number"]),
            pair[1]["source_page_number"],
        ))
        source_page_number = (
            candidates[0][1]["source_page_number"]
            if candidates
            else entry.get("original_source_page_number")
        )
        confidence = min(0.45, max(0.2, candidates[0][0] if candidates else 0.2))
        result.append({
            "outline_index": entry.get("outline_index"),
            "title": entry.get("title", ""),
            "level": entry.get("level", 1),
            "source_page_number": source_page_number,
            "confidence": confidence,
            "review_required": True,
            "source_outline_index": entry.get("outline_index"),
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
            "outline_index": entry.get("outline_index"),
            "level": entry.get("level", 1),
            "title": entry.get("title", ""),
            "processed_page_number": target["processed_page_number"],
            "source_page_number": target["source_page_number"],
            "confidence": confidence,
            "review_required": review_required,
            "provenance": entry.get("provenance", "source_outline"),
            "source_outline_index": entry.get("source_outline_index", entry.get("outline_index")),
        })
        had_review = had_review or review_required
    return remapped, "needs_review" if had_review or not remapped else "complete"


def build_canonical_contents(
    headings: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Build the standardized contents tree only from headings on retained pages."""
    page_by_source = {page["source_page_number"]: page for page in pages}
    heading_rows = [dict(item) for item in headings if item.get("source_page_number") in page_by_source]
    heading_rows.sort(key = lambda item: (
        item["source_page_number"],
        item.get("order", 0),
        item.get("level", 1),
        _normalized(item.get("title", "")),
    ))

    deduplicated: list[dict[str, Any]] = []
    index_by_key: dict[tuple[int, str], int] = {}
    for item in heading_rows:
        title_key = _normalized(item.get("title", ""))
        source_page_number = item.get("source_page_number")
        if not title_key or source_page_number not in page_by_source:
            continue
        key = (source_page_number, title_key)
        prior_index = index_by_key.get(key)
        if prior_index is None:
            index_by_key[key] = len(deduplicated)
            deduplicated.append(item)
            continue
        prior = deduplicated[prior_index]
        prior_review = prior.get("review_required", False)
        item_review = item.get("review_required", False)
        if item.get("confidence", 0) > prior.get("confidence", 0):
            deduplicated[prior_index] = {**prior, **item}
        deduplicated[prior_index]["review_required"] = prior_review or item_review

    page_stack: list[dict[str, Any]] = []
    canonical: list[dict[str, Any]] = []
    had_review = False
    for index, item in enumerate(deduplicated[:_MAX_CONTENTS_ENTRIES], start = 1):
        requested_level = item.get("level", 1)
        level = max(1, min(requested_level if type(requested_level) is int else 1, 8))
        if not page_stack:
            level = 1
        else:
            level = min(level, page_stack[-1]["level"] + 1)
        while page_stack and page_stack[-1]["level"] >= level:
            page_stack.pop()
        parent = page_stack[-1] if page_stack and page_stack[-1]["level"] == level - 1 else None
        source_page_number = item["source_page_number"]
        page = page_by_source[source_page_number]
        entry_id = f"contents-{index:05d}"
        entry = {
            "entry_id": entry_id,
            "parent_entry_id": parent["entry_id"] if parent else None,
            "level": level,
            "title": item["title"],
            "processed_page_number": page["processed_page_number"],
            "source_page_number": source_page_number,
            "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.0)))),
            "review_required": bool(item.get("review_required", False)),
            "provenance": item.get("provenance", "processed_page_heading"),
            "source_outline_index": None,
        }
        canonical.append(entry)
        page_stack.append({"entry_id": entry_id, "level": level})
        had_review = had_review or entry["review_required"]

    if not canonical:
        return [], "unavailable"
    return canonical, "needs_review" if had_review else "complete"
