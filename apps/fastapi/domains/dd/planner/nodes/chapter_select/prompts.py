"""chapter_select — prompt builder for the oversize-chapter split."""
from __future__ import annotations
import os
from . import params


def build_split_prompt(
    *,
    framework: str,
    title: str,
    description: str,
    units: list[dict],
    n_parts: int,
    other_titles: list[str],
    repair: str = "",
) -> str:
    """Static rubric first, per-chapter data last (KV-cache friendly)."""
    unit_block = "\n".join(
        f"[{u['id']}] {u['label']}"
        + (f" ({len(u['keys'])} pages of one reference page — keep together)" if len(u["keys"]) > 1 else "")
        + (f" — {u['summary']}" if u["summary"] else "")
        for u in units
    )
    n_docs = sum(len(u["keys"]) for u in units)
    return (
        f"You are restructuring ONE oversize chapter of a {framework} learning "
        f"book. It holds {n_docs} documentation pages — too many for a single "
        f"cohesive chapter — so split it into {n_parts} chapters (between 2 and "
        f"{params.SPLIT_MAX_PARTS} is acceptable if the content clearly calls for it).\n\n"
        f"RULES:\n"
        f"1. Every unit id below appears in EXACTLY ONE group. Never invent ids.\n"
        f"2. Units are indivisible: a unit marked 'keep together' is one reference "
        f"page and must stay whole.\n"
        f"3. Group units that teach the same sub-topic; each chapter must be "
        f"cohesive on its own and have at least {params.MIN_DOCS_PER_CHAPTER} pages and "
        f"roughly 15% or more of the chapter's pages.\n"
        f"4. Titles: concrete, 1-8 words, unique, and different from every "
        f"existing chapter title: {other_titles}. Never 'Overview', 'Introduction' "
        f"or 'Miscellaneous'.\n"
        f"5. Each description is ONE sentence about what the reader learns.\n\n"
        f'OUTPUT — STRICT JSON: {{"groups": [{{"title": "...", "description": "...", '
        f'"unit_ids": [0, 3, 4]}}, ...]}}\n\n'
        f"== CHAPTER TO SPLIT ==\n"
        f"title: {title}\n"
        f"description: {description}\n"
        f"== UNITS ==\n{unit_block}\n== END UNITS =="
        + (f"\n\nPRIOR OUTPUT WAS REJECTED: {repair}\nFix exactly that and return valid JSON." if repair else "")
    )


def _strip_common_prefix(labels: list[str]) -> list[str]:
    """Drop the slug prefix shared by every label (a single-URL dump titles its pages
    'site-com-llms-full-<page>')."""
    if len(labels) < 3:
        return labels
    pre = os.path.commonprefix(labels)
    cut = pre.rfind("-") + 1 if "-" in pre else 0
    return [l[cut:] or l for l in labels]


def build_name_prompt(
    *,
    framework: str,
    title: str,
    groups: list[list[int]],
    units: list[dict],
    other_titles: list[str],
    repair: str = "",
) -> str:
    """Name groups of pages that were already partitioned (the partition is computed). Every
    group lists its page names (up to SPLIT_NAME_LISTED) and the summaries of its first pages."""
    labels = _strip_common_prefix([u["label"] for u in units])
    blocks = []
    for gi, g in enumerate(groups):
        n_pages = sum(len(units[i]["keys"]) for i in g)
        shown = "; ".join(labels[i] for i in g[:params.SPLIT_NAME_LISTED])
        more = f" (+{len(g) - params.SPLIT_NAME_LISTED} more)" if len(g) > params.SPLIT_NAME_LISTED else ""
        summs = [units[i]["summary"][:params.SPLIT_NAME_SUMMARY_CHARS] for i in g if units[i]["summary"]]
        # spread the quoted summaries over the group instead of taking its first pages only
        step = max(1, len(summs) // params.SPLIT_NAME_SUMMARIES)
        quoted = " | ".join(summs[::step][:params.SPLIT_NAME_SUMMARIES])
        blocks.append(f"[{gi}] {n_pages} pages: {shown}{more}" + (f"\n    summaries: {quoted}" if quoted else ""))
    return (
        f"A {framework} learning book has an oversize chapter, '{title}', that was split into "
        f"{len(groups)} groups of related documentation pages. Name each group as a chapter.\n\n"
        f"RULES:\n"
        f"1. Titles: concrete, 1-8 words, unique, and different from every existing chapter title: "
        f"{other_titles}. Never 'Overview', 'Introduction' or 'Miscellaneous'.\n"
        f"2. A title names what MOST of the group's pages have in common, judged from ALL the page "
        f"names listed, not from the first ones. Pages named like module paths "
        f"(`pkg-subpkg-module`) share a subsystem: name that subsystem. Two groups must not share "
        f"a title idea.\n"
        f"3. Each description is ONE sentence about what the reader learns from the group as a whole.\n"
        f"4. Return one entry per group id, ids exactly as shown.\n\n"
        f'OUTPUT - STRICT JSON: {{"groups": [{{"id": 0, "title": "...", "description": "..."}}, ...]}}\n\n'
        f"== GROUPS ==\n" + "\n".join(blocks) + "\n== END GROUPS =="
        + (f"\n\nPRIOR OUTPUT WAS REJECTED: {repair}\nFix exactly that and return valid JSON." if repair else "")
    )
