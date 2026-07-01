"""Prompt builders: chapter proposer + USC vote picker + corpus block renderers."""
from __future__ import annotations

from typing import Optional

from .params import (
    CONCEPTS_MAX,
    CONCEPTS_MIN,
    PROPOSALS_MAX,
    PROPOSALS_MIN,
    TITLE_MAX_WORDS,
    TITLE_MIN_WORDS,
)


def _render_distillates_block(
    distillates: dict[str, dict], source_keys: list[str],
) -> str:
    """Render distillates into a token-tight block for the proposer prompt."""
    lines: list[str] = []
    for i, key in enumerate(source_keys, 1):
        d = distillates.get(key) or {}
        summary = (d.get("summary") or "").strip()
        terms = d.get("key_terms") or []
        if not summary:
            continue
        terms_str = ", ".join(terms[:8])
        lines.append(
            f"[{i}] {key}\n    summary: {summary}\n    terms: {terms_str}"
        )
    return "\n".join(lines)


def _render_full_bodies_block(
    bodies_by_key: dict[str, str], source_keys: list[str],
    body_chars_per_doc: int,
) -> str:
    """Render full doc bodies (truncated) for small-N pass-through."""
    parts: list[str] = []
    for i, key in enumerate(source_keys, 1):
        body = (bodies_by_key.get(key) or "").strip()
        if not body:
            continue
        snippet = body[:body_chars_per_doc]
        parts.append(f"=== DOC [{i}] {key} ===\n{snippet}\n")
    return "\n".join(parts)


def build_propose_prompt(
    *,
    framework: str,
    source_keys: list[str],
    distillates: Optional[dict[str, dict]],
    bodies_by_key: Optional[dict[str, str]],
    seeds: dict,
    body_chars_per_doc: int,
    target_chapters: int,
) -> str:
    """Proposer prompt. distillates=None → full-body pass-through (small N). LangFuse template `dd.planner.chapter_propose` wins over local f-string when deployed; local is the source of truth."""
    if distillates is not None:
        corpus_block = _render_distillates_block(distillates, source_keys)
        corpus_label = "DOC DISTILLATES"
    else:
        corpus_block = _render_full_bodies_block(
            bodies_by_key or {}, source_keys, body_chars_per_doc,
        )
        corpus_label = "DOC BODIES (small-N pass-through)"

    headings_block = ", ".join(seeds.get("headings") or []) or "(none)"
    namespaces_block = ", ".join(seeds.get("namespaces") or []) or "(none)"

    try:
        from infra.langfuse.prompts import get_prompt as _lf_get_prompt
        _rendered = _lf_get_prompt(
            "dd.planner.chapter_propose",
            label     = "production",
            variables = {
                "framework":        framework,
                "target_chapters":  target_chapters,
                "n_source_keys":    len(source_keys),
                "proposals_min":    PROPOSALS_MIN,
                "proposals_max":    PROPOSALS_MAX,
                "title_min_words":  TITLE_MIN_WORDS,
                "title_max_words":  TITLE_MAX_WORDS,
                "concepts_min":     CONCEPTS_MIN,
                "concepts_max":     CONCEPTS_MAX,
                "headings_block":   headings_block,
                "namespaces_block": namespaces_block,
                "corpus_label":     corpus_label,
                "corpus_block":     corpus_block,
            },
        )
        if _rendered:
            return _rendered
    except Exception:
        pass

    return (
        f"You are the Chapter Planner for the {framework} documentation.\n\n"
        f"Your job: propose a balanced set of about {target_chapters} "
        f"chapters (TARGET={target_chapters}, sized to this corpus of "
        f"{len(source_keys)} docs; stay close to it, hard range "
        f"{PROPOSALS_MIN}-{PROPOSALS_MAX}) that COVER THE FULL SURFACE AREA "
        f"of this framework. Too FEW chapters forces unrelated topics to "
        f"share one over-broad chapter; aim for ~{target_chapters} so each "
        f"chapter is a cohesive, single-topic unit. Each chapter must:\n"
        f"  - have a concrete, specific title ({TITLE_MIN_WORDS}-"
        f"{TITLE_MAX_WORDS} words; no generic 'Introduction'/"
        f"'Overview'/'Conclusion')\n"
        f"  - cover a DISTINCT topic from every other chapter\n"
        f"  - be backed by ≥3 docs from the corpus\n"
        f"  - list {CONCEPTS_MIN}-{CONCEPTS_MAX} specific concepts/"
        f"identifiers/commands that belong in it\n\n"
        f"== STRUCTURAL SIGNALS extracted from the corpus ==\n"
        f"Top recurring headings (appear in ≥2 docs):\n"
        f"  {headings_block}\n"
        f"File-tree namespaces (likely top-level groupings):\n"
        f"  {namespaces_block}\n\n"
        f"== CORPUS — {len(source_keys)} {corpus_label} ==\n"
        f"{corpus_block}\n"
        f"== END CORPUS ==\n\n"
        f"OUTPUT — STRICT JSON:\n"
        f"{{\n"
        f'  "proposals": [\n'
        f'    {{\n'
        f'      "title":        "Concrete Topic Name",\n'
        f'      "description":  "One sentence describing what readers '
        f'learn here.",\n'
        f'      "key_concepts": ["concept1", "command-name", "TypeName", ...]\n'
        f'    }},\n'
        f'    ...\n'
        f'  ]\n'
        f"}}\n\n"
        f"HARD RULES:\n"
        f"1. Between {PROPOSALS_MIN} and {PROPOSALS_MAX} chapters.\n"
        f"2. Titles UNIQUE case-insensitively.\n"
        f"3. NEVER use generic content-type names ('Introduction', "
        f"'Conclusion', 'Overview', 'Getting Started', 'About', "
        f"'Background', 'References') as a chapter title.\n"
        f"4. PREFER chapters that correspond to structural signals "
        f"above (a 'commands/plugin' namespace → likely a 'Plugin "
        f"Management' chapter).\n"
        f"5. For CLI tools: ensure every TOP-LEVEL subcommand visible "
        f"in the corpus has chapter coverage somewhere.\n"
        f"6. Avoid mega-chapters: if your draft has any chapter that "
        f"would absorb >40% of the docs, SPLIT it.\n\n"
        f"Respond ONLY with valid JSON. No prose, no markdown wrap."
    )


def build_usc_vote_prompt(
    *,
    framework: str,
    candidates_summary: list[dict],
) -> str:
    """Pick the best of N proposal samples by balance + coverage rubric."""
    lines: list[str] = []
    for i, c in enumerate(candidates_summary):
        n_chapters = c.get("n_chapters", 0)
        titles = c.get("titles") or []
        max_concept_count = c.get("max_concept_count", 0)
        lines.append(
            f"CANDIDATE {i}: {n_chapters} chapters, max concepts in any "
            f"chapter = {max_concept_count}"
        )
        lines.append("  Titles: " + ", ".join(titles[:PROPOSALS_MAX]))
    block = "\n".join(lines)
    return (
        f"You are the Universal Self-Consistency picker. Pick the BEST "
        f"of {len(candidates_summary)} chapter proposal sets for "
        f"{framework}.\n\n"
        f"== CANDIDATES ==\n{block}\n\n"
        f"Pick by:\n"
        f"  1. Balance (no single chapter dominates concepts)\n"
        f"  2. Coverage (more chapters = more coverage, up to {PROPOSALS_MAX})\n"
        f"  3. Specificity (concrete titles, not 'Overview'/'Introduction')\n\n"
        f"OUTPUT — STRICT JSON:\n"
        f'{{"chosen_index": <int>, "reason": "<short>"}}'
    )
