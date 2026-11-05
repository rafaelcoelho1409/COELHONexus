"""LLM judge prompts, head+tail truncation, and anchor descriptors. Pure module; no I/O."""
from __future__ import annotations
from . import params



def _article(word: str) -> str:
    """a/an for the anchor sentence (new categories like AI/Infrastructure
    need 'an')."""
    return "an" if (word or "")[:1].lower() in "aeiou" else "a"


def build_positive_descriptor(entry: dict, summary: str = "") -> str:
    """Anchor prompt for the framework. Uses the catalog name + category.
    `summary` (Tier 2 llms.txt blockquote, "" otherwise) appends the author's
    own description — stats-only enrichment, never affects the judge prompt."""
    name = entry.get("name") or entry.get("slug") or "unknown"
    category = entry.get("category") or ""
    if category:
        base = (
            f"Documentation for {name}, {_article(category)} {category} "
            f"library / framework. "
            f"Teaching content: tutorials, guides, API reference, how-to "
            f"articles, conceptual explanations."
        )
    else:
        base = (
            f"Documentation for {name}. Teaching content: tutorials, guides, "
            f"API reference, how-to articles, conceptual explanations."
        )
    if summary:
        base += f" About {name}: {summary}"
    return base


def head_tail_truncate(body: str) -> str:
    """Head+tail truncation: LLMs attend most to start+end, middle is wasted attention for binary classification (arXiv 2403.12799: head+tail beats head-only by 1-3 F1). Full body when it fits."""
    s = (body or "").strip()
    if not s:
        return "(empty page)"
    if len(s) <= params.JUDGE_BODY_MIN_FOR_SPLIT:
        # Fits in combined window — send the WHOLE page, no fake gap.
        return s
    return (
        s[:params.JUDGE_HEAD_CHARS]
        + params.JUDGE_HEAD_TAIL_SEP
        + s[-params.JUDGE_TAIL_CHARS:]
    )


def build_judge_prompt(
    framework_name: str, framework_category: str, body: str, summary: str = "",
) -> str:
    """Single-shot KEEP/DROP rubric; unambiguous instruction so model returns one-word verdict at temperature=0.

    `summary` (Tier 2 llms.txt blockquote, "" for every other tier) adds one
    author-written sentence to the static prefix — still prefix-positioned, so
    KV-cache reuse is preserved. "" yields the exact legacy string for all
    consonant-initial categories (every pre-existing one), so Tier 1 prompts
    are byte-identical to before; only new vowel-initial categories (AI,
    Infrastructure) take "an"."""
    cat_clause = (
        f", {_article(framework_category)} {framework_category} library/framework"
        if framework_category else ""
    )
    truncated = head_tail_truncate(body)
    summary_clause = f" About {framework_name}: {summary}" if summary else ""
    return (
        f"You are filtering pages from the official documentation site of "
        f"{framework_name}{cat_clause}.{summary_clause}\n\n"
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
