"""Prompt builders: chapter proposer + USC vote picker + corpus block renderers."""
from __future__ import annotations
import infra
from infra.langfuse.prompts import with_langfuse_override
from . import params

from typing import Optional


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


def _structure_signal(source_structure: str) -> str:
    if not source_structure:
        return ""
    return (
        "SOURCE STRUCTURE (the documentation's own navigation order; pages that "
        "share a parent page are ONE family; site sections are URL directories):\n"
        f"{source_structure}\n"
    )


SECTION_RULE = (
    "a site section (URL directory) listed under SOURCE STRUCTURE whose pages are all the "
    "same kind of page — one page per provider, integration, plugin, loader or model — "
    "gets its OWN chapter named after the section; do not scatter such catalog pages "
    "across topical chapters."
)


def _structure_rules(source_structure: str) -> str:
    if not source_structure:
        return ""
    out = (
        "8. Keep every family (parent page, see SOURCE STRUCTURE) whole inside ONE chapter; "
        "never scatter a family's pages across chapters. Prefer chapters that follow the "
        "source structure's own grouping and order.\n"
        "9. Do NOT create chapters for overview/landing pages, platform support, debugging "
        "or diagnostics notes — those cross-cutting pages are collected into a separate "
        "Foundations chapter automatically.\n"
    )
    if domain_sections_listed(source_structure):
        out += f"10. Also: {SECTION_RULE}\n"
    return out


def domain_sections_listed(source_structure: str) -> bool:
    return "Site sections" in (source_structure or "")


def _extra_signal_block(source_structure: str) -> str:
    """Appended to a LangFuse-rendered prompt: the deployed template predates this
    node's local rules (it still says "split above 40%"), and LangFuse wins when
    reachable. Always carries the tightened size rule; adds SOURCE STRUCTURE (the
    docs' own navigation order, parent pages as families) and the two rules that
    depend on it when the corpus has multi-page families."""
    out = (
        "\n\nOVERRIDING RULE 6: split any chapter that would absorb more than 30% "
        "of the docs (not 40%), and any chapter whose docs span clearly unrelated "
        "sub-themes."
    )
    if source_structure:
        out += (
            "\n\n== SOURCE STRUCTURE (the documentation's own navigation order; pages "
            "that share a parent page are ONE family) ==\n"
            f"{source_structure}\n"
            "== END SOURCE STRUCTURE ==\n"
            "ADDITIONAL RULES:\n"
            "- Keep every family whole inside ONE chapter; never scatter a family's pages "
            "across chapters. Prefer chapters that follow this structure's own grouping and order.\n"
            "- Do NOT create chapters for overview/landing pages, platform support, debugging "
            "or diagnostics notes — those cross-cutting pages are collected into a separate "
            "Foundations chapter automatically."
        )
        if domain_sections_listed(source_structure):
            out += f"\n- Also: {SECTION_RULE}"
    return out + "\n\nRespond ONLY with valid JSON. No prose, no markdown wrap."


def build_propose_prompt(
    *,
    framework: str,
    source_keys: list[str],
    distillates: Optional[dict[str, dict]],
    bodies_by_key: Optional[dict[str, str]],
    seeds: dict,
    body_chars_per_doc: int,
    target_chapters: int,
    source_structure: str = "",
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
        _rendered = infra.langfuse.prompts.get_prompt(
            "dd.planner.chapter_propose",
            label     = "production",
            variables = {
                "framework":        framework,
                "target_chapters":  target_chapters,
                "n_source_keys":    len(source_keys),
                "proposals_min":    params.PROPOSALS_MIN,
                "proposals_max":    params.PROPOSALS_MAX,
                "title_min_words":  params.TITLE_MIN_WORDS,
                "title_max_words":  params.TITLE_MAX_WORDS,
                "concepts_min":     params.CONCEPTS_MIN,
                "concepts_max":     params.CONCEPTS_MAX,
                "headings_block":   headings_block,
                "namespaces_block": namespaces_block,
                "corpus_label":     corpus_label,
                "corpus_block":     corpus_block,
            },
        )
        if _rendered:
            # The deployed template predates the SOURCE STRUCTURE signal and the
            # family/foundations rules — append them so they apply on this path too.
            return _rendered + _extra_signal_block(source_structure)
    except Exception:
        pass

    return (
        f"You are the Chapter Planner for the {framework} documentation.\n\n"
        f"Your job: propose a balanced set of about {target_chapters} "
        f"chapters (TARGET={target_chapters}, sized to this corpus of "
        f"{len(source_keys)} docs; stay close to it, hard range "
        f"{params.PROPOSALS_MIN}-{params.PROPOSALS_MAX}) that COVER THE FULL SURFACE AREA "
        f"of this framework. Too FEW chapters forces unrelated topics to "
        f"share one over-broad chapter; aim for ~{target_chapters} so each "
        f"chapter is a cohesive, single-topic unit. Each chapter must:\n"
        f"  - have a concrete, specific title ({params.TITLE_MIN_WORDS}-"
        f"{params.TITLE_MAX_WORDS} words; no generic 'Introduction'/"
        f"'Overview'/'Conclusion')\n"
        f"  - cover a DISTINCT topic from every other chapter\n"
        f"  - be backed by ≥3 docs from the corpus\n"
        f"  - list {params.CONCEPTS_MIN}-{params.CONCEPTS_MAX} specific concepts/"
        f"identifiers/commands that belong in it\n\n"
        f"== STRUCTURAL SIGNALS extracted from the corpus ==\n"
        f"Top recurring headings (appear in ≥2 docs):\n"
        f"  {headings_block}\n"
        f"File-tree namespaces (likely top-level groupings):\n"
        f"  {namespaces_block}\n"
        f"{_structure_signal(source_structure)}\n"
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
        f"1. Between {params.PROPOSALS_MIN} and {params.PROPOSALS_MAX} chapters.\n"
        f"2. Titles UNIQUE case-insensitively.\n"
        f"3. NEVER use generic content-type names ('Introduction', "
        f"'Conclusion', 'Overview', 'Getting Started', 'About', "
        f"'Background', 'References') as a chapter title.\n"
        f"4. PREFER chapters that correspond to structural signals "
        f"above (a 'commands/plugin' namespace → likely a 'Plugin "
        f"Management' chapter).\n"
        f"5. For CLI tools: ensure every TOP-LEVEL subcommand visible "
        f"in the corpus has chapter coverage somewhere.\n"
        f"6. Avoid mega-chapters AND grab-bag chapters: if your draft "
        f"has any chapter that would absorb >30% of the docs, SPLIT "
        f"it. Also split a chapter whose docs span clearly unrelated "
        f"sub-themes even if it stays under that threshold — "
        f"semantic incoherence matters as much as raw size.\n"
        f"7. Language-specific SDK/API reference material (client "
        f"library references, 'hosting the SDK', programmatic/"
        f"scripted usage, streaming responses, structured output "
        f"from the SDK) is its OWN topic, distinct from protocol/"
        f"tool-extension mechanics (MCP servers, plugin marketplace) "
        f"and from CI/CD or configuration. Group ALL such docs into "
        f"one dedicated 'SDK Reference' (or similarly concrete) "
        f"chapter rather than letting them scatter into whichever "
        f"adjacent chapter happens to mention extensibility.\n"
        f"{_structure_rules(source_structure)}"
        f"\nRespond ONLY with valid JSON. No prose, no markdown wrap."
    )


@with_langfuse_override("dd.planner.chapter_propose.usc_vote")
def build_usc_vote_prompt(
    *,
    framework: str,
    candidates_summary: list[dict],
    target_chapters: int = 0,
) -> str:
    """Tie-break between proposal samples whose evidence scores are close (the score —
    docs matching no chapter, largest chapter share, distance from target — already
    ruled out the clearly worse ones)."""
    lines: list[str] = []
    for i, c in enumerate(candidates_summary):
        n_chapters = c.get("n_chapters", 0)
        titles = c.get("titles") or []
        max_concept_count = c.get("max_concept_count", 0)
        evidence = ""
        if "unmatched_pct" in c:
            evidence = (
                f"; {c['unmatched_pct']}% of docs match no chapter, largest chapter "
                f"holds {c['largest_share_pct']}% of docs"
            )
        lines.append(
            f"CANDIDATE {i}: {n_chapters} chapters, max concepts in any "
            f"chapter = {max_concept_count}{evidence}"
        )
        lines.append("  Titles: " + ", ".join(titles[:params.PROPOSALS_MAX]))
    block = "\n".join(lines)
    return (
        f"You are the Universal Self-Consistency picker. Pick the BEST "
        f"of {len(candidates_summary)} chapter proposal sets for "
        f"{framework}.\n\n"
        f"== CANDIDATES ==\n{block}\n\n"
        + (f"The target is about {target_chapters} chapters.\n" if target_chapters else "")
        + f"Pick by:\n"
        f"  1. Coverage (every topic of the framework has its own chapter; few docs match no chapter)\n"
        f"  2. Balance (no single chapter dominates; a chapter count near the target)\n"
        f"  3. Specificity (concrete titles, not 'Overview'/'Introduction')\n\n"
        f"OUTPUT — STRICT JSON:\n"
        f'{{"chosen_index": <int>, "reason": "<short>"}}'
    )
