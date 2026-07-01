"""LLM judge prompts, head+tail truncation, and anchor descriptors. Pure module; no I/O."""
from __future__ import annotations

from .params import (
    JUDGE_BODY_MIN_FOR_SPLIT,
    JUDGE_HEAD_CHARS,
    JUDGE_HEAD_TAIL_SEP,
    JUDGE_TAIL_CHARS,
)


def build_positive_descriptor(entry: dict) -> str:
    """Anchor prompt for the framework. Uses the catalog name + category."""
    name = entry.get("name") or entry.get("slug") or "unknown"
    category = entry.get("category") or ""
    if category:
        return (
            f"Documentation for {name}, a {category} library / framework. "
            f"Teaching content: tutorials, guides, API reference, how-to "
            f"articles, conceptual explanations."
        )
    return (
        f"Documentation for {name}. Teaching content: tutorials, guides, "
        f"API reference, how-to articles, conceptual explanations."
    )


def head_tail_truncate(body: str) -> str:
    """Head+tail truncation: LLMs attend most to start+end, middle is wasted attention for binary classification (arXiv 2403.12799: head+tail beats head-only by 1-3 F1). Full body when it fits."""
    s = (body or "").strip()
    if not s:
        return "(empty page)"
    if len(s) <= JUDGE_BODY_MIN_FOR_SPLIT:
        # Fits in combined window — send the WHOLE page, no fake gap.
        return s
    return (
        s[:JUDGE_HEAD_CHARS]
        + JUDGE_HEAD_TAIL_SEP
        + s[-JUDGE_TAIL_CHARS:]
    )


def build_judge_prompt(
    framework_name: str, framework_category: str, body: str,
) -> str:
    """Single-shot KEEP/DROP rubric; unambiguous instruction so model returns one-word verdict at temperature=0."""
    cat_clause = (
        f", a {framework_category} library/framework"
        if framework_category else ""
    )
    truncated = head_tail_truncate(body)
    return (
        f"You are filtering pages from the official documentation site of "
        f"{framework_name}{cat_clause}.\n\n"
        f"Decide if this page is:\n"
        f"  KEEP → teaching content (tutorials, guides, API reference, "
        f"how-to articles, conceptual explanations of how to use the library)\n"
        f"  DROP → repository meta-content (code of conduct, contributing "
        f"guidelines, sponsor lists, conference talks or event pages, "
        f"blog posts, changelog dumps, release notes, governance policies, "
        f"license text, generated index pages with no real content)\n\n"
        f"Respond with EXACTLY ONE WORD: KEEP or DROP.\n\n"
        f"--- Page content (long pages truncated as `head[…]tail`; "
        f"the `[…]` marker means content was elided between the head "
        f"and tail samples) ---\n"
        f"{truncated}\n"
        f"--- End page content ---\n\n"
        f"Answer (KEEP or DROP):"
    )
