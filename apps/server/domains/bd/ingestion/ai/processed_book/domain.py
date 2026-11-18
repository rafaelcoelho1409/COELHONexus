"""Pure heading discovery, contents construction, and processed page-number mapping."""
from __future__ import annotations
from . import keys, params, patterns

from difflib import SequenceMatcher
from typing import Any


def _normalized(value: str) -> str:
    return patterns.WORD_SEPARATOR_RE.sub(" ", value.casefold()).strip()


def _similarity(title: str, evidence: list[str]) -> float:
    left = _normalized(title)
    if not left:
        return params.MIN_NORMALIZED_SCORE
    best = params.MIN_NORMALIZED_SCORE
    for item in evidence:
        right = _normalized(item)
        if not right:
            continue
        if left == right:
            score = params.MAX_NORMALIZED_SCORE
        elif f" {left} " in f" {right} " or f" {right} " in f" {left} ":
            score = min(params.SUBSTRING_SIMILARITY_CEILING, SequenceMatcher(None, left, right).ratio())
        else:
            score = SequenceMatcher(None, left, right).ratio()
        best = max(best, score)
    return best


def _is_main_structure_heading(title: str) -> bool:
    normalized = _normalized(title)
    return patterns.MAIN_STRUCTURE_HEADING_RE.match(normalized) is not None


def _is_noise_heading(title: str) -> bool:
    normalized = _normalized(title)
    return (
        not normalized
        or normalized in params.NAVIGATION_HEADINGS
        or patterns.NUMBER_OR_ROMAN_NUMERAL_RE.fullmatch(normalized) is not None
        or patterns.PART_OR_CHAPTER_NUMERAL_RE.fullmatch(normalized) is not None
        or any(normalized.startswith(prefix) for prefix in params.NOISE_HEADING_PREFIXES)
    )


def _first_main_structure_page(pages: list[Any]) -> int | None:
    for page in pages:
        if page.get("role") in params.NON_MAIN_STRUCTURE_ROLES:
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
        for index, match in enumerate(patterns.MARKDOWN_HEADING_RE.finditer(markdown))
        if match.group(2).strip()
    ][:params.MAX_PAGE_HEADINGS]


def heading_evidence(markdown: str, section_path: list[str]) -> list[str]:
    headings = [item["title"] for item in markdown_headings(markdown)]
    return list(dict.fromkeys([*section_path, *headings]))[:params.MAX_HEADING_EVIDENCE]


def canonical_heading_candidates(page_evidence: list[Any]) -> list[dict[str, Any]]:
    """Collect actual page headings and section-path transitions from selected pages only."""
    valid_pages = sorted(
        (
            row for row in page_evidence
            if isinstance(row, dict)
            and type(row.get("source_page_number")) is int
            and row["source_page_number"] >= params.MIN_SOURCE_PAGE_NUMBER
        ),
        key = lambda row: row["source_page_number"],
    )
    first_main_page = _first_main_structure_page(valid_pages)
    result: list[dict[str, Any]] = []
    previous_path: list[str] = []
    for page in valid_pages:
        if page.get("role") in params.NON_CONTENT_ROLES:
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
                "title": section_path[path_index][:params.MAX_TITLE_CHARS],
                "level": min(path_index + params.MIN_HEADING_LEVEL, params.MAX_HEADING_LEVEL),
                "order": path_index,
                "source_page_number": source_page_number,
                "confidence": params.SECTION_PATH_HEADING_CONFIDENCE,
                "provenance": "section_path",
            })
        section_path_titles = [_normalized(value) for value in section_path]
        raw_headings = page.get("headings", [])
        if isinstance(raw_headings, list):
            for item in raw_headings[:params.MAX_PAGE_HEADINGS]:
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
                    "title": title.strip()[:params.MAX_TITLE_CHARS],
                    "level": max(params.MIN_HEADING_LEVEL, min(level, params.MAX_HEADING_LEVEL)),
                    "order": params.PROCESSED_PAGE_HEADING_ORDER_OFFSET + (
                        order if type(order) is int else params.MIN_ZERO_BASED_INDEX
                    ),
                    "source_page_number": source_page_number,
                    "confidence": params.PROCESSED_PAGE_HEADING_CONFIDENCE,
                    "provenance": "processed_page_heading",
                })

        seen_on_page: set[str] = set()
        for row in page_rows:
            title_key = _normalized(row["title"])
            if _is_noise_heading(row["title"]) or title_key in seen_on_page:
                continue
            seen_on_page.add(title_key)
            result.append(row)
            if len(result) >= params.MAX_CONTENTS_ENTRIES:
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
            or row["source_page_number"] < params.MIN_SOURCE_PAGE_NUMBER
            or not isinstance(row.get("evidence"), list)
            or row.get("role") in params.NON_CONTENTS_CANDIDATE_ROLES
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
        if (
            not isinstance(title, str)
            or not title.strip()
            or type(source_page) is not int
            or source_page < params.MIN_SOURCE_PAGE_NUMBER
        ):
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
        matching = [(score, row) for score, row in ranked if score >= params.MIN_CONTENTS_MATCH_SIMILARITY]
        chosen = (
            matching[:params.MAX_MATCHED_CONTENTS_CANDIDATES]
            if matching else ranked[:params.MAX_FALLBACK_CONTENTS_CANDIDATES]
        )
        result.append({
            "outline_index": index,
            "title": title.strip()[:params.MAX_TITLE_CHARS],
            "level": max(
                params.MIN_HEADING_LEVEL,
                min(
                    params.MAX_HEADING_LEVEL,
                    item.get("level", params.MIN_HEADING_LEVEL)
                    if type(item.get("level")) is int else params.MIN_HEADING_LEVEL,
                ),
            ),
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
            if type(page_number) is not int or page_number < params.MIN_SOURCE_PAGE_NUMBER:
                continue
            score = candidate.get("similarity")
            if type(score) not in (int, float) or score <= params.MIN_NORMALIZED_SCORE:
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
                "confidence": params.FALLBACK_CONFIDENCE_FLOOR,
                "review_required": True,
            })
            continue

        best_score, best = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else params.MIN_NORMALIZED_SCORE
        page_number = best["source_page_number"]
        source_anchor_agrees = (
            page_number >= entry["original_source_page_number"]
            and best_score >= params.STRONG_MATCH_SIMILARITY
        )
        decisive_score = (
            len(_normalized(entry.get("title", "")).split()) >= params.DECISIVE_MATCH_MIN_TITLE_WORDS
            and best_score >= params.DECISIVE_MATCH_SIMILARITY
            and best_score - second_score >= params.DECISIVE_MATCH_MIN_SIMILARITY_GAP
        )
        if best_score >= params.STRONG_MATCH_SIMILARITY or source_anchor_agrees or decisive_score:
            resolved.append({
                "outline_index": entry["outline_index"],
                "title": entry["title"],
                "level": entry["level"],
                "source_page_number": page_number,
                "confidence": min(
                    params.RESOLVED_CONFIDENCE_CEILING,
                    max(params.RESOLVED_CONFIDENCE_FLOOR, best_score),
                ),
                "review_required": best_score < params.REVIEW_REQUIRED_SIMILARITY_THRESHOLD,
            })
        elif best_score >= params.MIN_AMBIGUOUS_MATCH_SIMILARITY and len(ranked) > 1:
            ambiguous.append(entry)
        else:
            resolved.append({
                "outline_index": entry["outline_index"],
                "title": entry["title"],
                "level": entry["level"],
                "source_page_number": page_number,
                "confidence": min(
                    params.FALLBACK_CONFIDENCE_CEILING,
                    max(params.FALLBACK_CONFIDENCE_FLOOR, best_score),
                ),
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
            if type(page_number) is not int or page_number < params.MIN_SOURCE_PAGE_NUMBER:
                continue
            score = candidate.get("similarity")
            if type(score) not in (int, float) or score <= params.MIN_NORMALIZED_SCORE:
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
        confidence = min(
            params.FALLBACK_CONFIDENCE_CEILING,
            max(
                params.FALLBACK_CONFIDENCE_FLOOR,
                candidates[0][0] if candidates else params.FALLBACK_CONFIDENCE_FLOOR,
            ),
        )
        result.append({
            "outline_index": entry.get("outline_index"),
            "title": entry.get("title", ""),
            "level": entry.get("level", params.MIN_HEADING_LEVEL),
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
        if type(source_page) is not int or source_page < params.MIN_SOURCE_PAGE_NUMBER:
            had_review = True
            continue
        target = page_by_source.get(source_page)
        review_required = bool(entry.get("review_required", False))
        confidence = entry.get("confidence", params.MIN_NORMALIZED_SCORE)
        if target is None:
            replacement = next((number for number in source_numbers if number >= source_page), source_numbers[-1])
            target = page_by_source[replacement]
            confidence = min(
                float(confidence) if type(confidence) in (int, float) else params.MIN_NORMALIZED_SCORE,
                params.REMAP_CONFIDENCE_CEILING,
            )
            review_required = True
        remapped.append({
            "outline_index": entry.get("outline_index"),
            "level": entry.get("level", params.MIN_HEADING_LEVEL),
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
        item.get("order", params.MIN_ZERO_BASED_INDEX),
        item.get("level", params.MIN_HEADING_LEVEL),
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
        if (
            item.get("confidence", params.MIN_NORMALIZED_SCORE)
            > prior.get("confidence", params.MIN_NORMALIZED_SCORE)
        ):
            deduplicated[prior_index] = {**prior, **item}
        deduplicated[prior_index]["review_required"] = prior_review or item_review

    page_stack: list[dict[str, Any]] = []
    canonical: list[dict[str, Any]] = []
    had_review = False
    for index, item in enumerate(
        deduplicated[:params.MAX_CONTENTS_ENTRIES],
        start = keys.CONTENTS_ENTRY_ID_START,
    ):
        requested_level = item.get("level", params.MIN_HEADING_LEVEL)
        level = max(
            params.MIN_HEADING_LEVEL,
            min(
                requested_level if type(requested_level) is int else params.MIN_HEADING_LEVEL,
                params.MAX_HEADING_LEVEL,
            ),
        )
        if not page_stack:
            level = params.MIN_HEADING_LEVEL
        else:
            level = min(level, page_stack[-1]["level"] + 1)
        while page_stack and page_stack[-1]["level"] >= level:
            page_stack.pop()
        parent = page_stack[-1] if page_stack and page_stack[-1]["level"] == level - 1 else None
        source_page_number = item["source_page_number"]
        page = page_by_source[source_page_number]
        entry_id = keys.contents_entry_id(index)
        entry = {
            "entry_id": entry_id,
            "parent_entry_id": parent["entry_id"] if parent else None,
            "level": level,
            "title": item["title"],
            "processed_page_number": page["processed_page_number"],
            "source_page_number": source_page_number,
            "confidence": max(
                params.MIN_NORMALIZED_SCORE,
                min(
                    params.MAX_NORMALIZED_SCORE,
                    float(item.get("confidence", params.MIN_NORMALIZED_SCORE)),
                ),
            ),
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
