"""chapter_select — prompt builder for the oversize-chapter split."""
from __future__ import annotations
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
