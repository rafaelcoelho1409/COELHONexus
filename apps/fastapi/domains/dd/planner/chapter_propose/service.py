"""chapter_propose — pure helpers (no I/O, no LLM)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .constants import (
    _CONCEPT_CHARS_MAX,
    _CONCEPT_CHARS_MIN,
    _CONCEPTS_MAX,
    _CONCEPTS_MIN,
    _DESCRIPTION_CHARS_MAX,
    _DESCRIPTION_CHARS_MIN,
    _PROPOSALS_MAX,
    _PROPOSALS_MIN,
    _SEED_MAX_HEADINGS,
    _SEED_MAX_NAMESPACES,
    _TITLE_MAX_WORDS,
    _TITLE_MIN_WORDS,
    _CLI_PATTERN_RE,
)


# -------------------------------------------------------------- #
# Pydantic schemas                                                #
# -------------------------------------------------------------- #
class ChapterProposal(BaseModel):
    """One candidate chapter from the proposer LLM."""
    title: str = Field(
        description=(
            f"{_TITLE_MIN_WORDS}-{_TITLE_MAX_WORDS} words. Concrete noun "
            f"phrase. Avoid generic 'Introduction', 'Overview', "
            f"'Conclusion' — name the specific topic."
        ),
    )
    description: str = Field(
        description=(
            f"{_DESCRIPTION_CHARS_MIN}-{_DESCRIPTION_CHARS_MAX} chars. "
            f"One sentence describing what readers learn in this chapter."
        ),
    )
    key_concepts: list[str] = Field(
        description=(
            f"{_CONCEPTS_MIN}-{_CONCEPTS_MAX} technical concepts/identifiers/"
            f"commands that belong in this chapter. Specific names, not "
            f"abstract topics."
        ),
    )

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        n = len(s.split())
        if not (_TITLE_MIN_WORDS <= n <= _TITLE_MAX_WORDS):
            raise ValueError(
                f"title must be {_TITLE_MIN_WORDS}-{_TITLE_MAX_WORDS} "
                f"words; got {n}"
            )
        return s

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_DESCRIPTION_CHARS_MIN <= len(s) <= _DESCRIPTION_CHARS_MAX):
            raise ValueError(
                f"description must be {_DESCRIPTION_CHARS_MIN}-"
                f"{_DESCRIPTION_CHARS_MAX} chars; got {len(s)}"
            )
        return s

    @field_validator("key_concepts")
    @classmethod
    def _validate_concepts(cls, v: list[str]) -> list[str]:
        if not (_CONCEPTS_MIN <= len(v) <= _CONCEPTS_MAX):
            raise ValueError(
                f"key_concepts count must be {_CONCEPTS_MIN}-"
                f"{_CONCEPTS_MAX}; got {len(v)}"
            )
        out: list[str] = []
        seen: set[str] = set()
        for c in v:
            s = " ".join(c.strip().split())
            if not (_CONCEPT_CHARS_MIN <= len(s) <= _CONCEPT_CHARS_MAX):
                raise ValueError(
                    f"concept length must be {_CONCEPT_CHARS_MIN}-"
                    f"{_CONCEPT_CHARS_MAX}; got {len(s)}"
                )
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
        if len(out) < _CONCEPTS_MIN:
            raise ValueError(
                f"after dedup only {len(out)} key_concepts "
                f"(minimum {_CONCEPTS_MIN})"
            )
        return out


class ChapterProposalList(BaseModel):
    """LLM output — a list of chapter proposals."""
    proposals: list[ChapterProposal] = Field(
        description=(
            f"{_PROPOSALS_MIN}-{_PROPOSALS_MAX} chapter proposals covering "
            f"the full corpus surface area. Each chapter is a distinct "
            f"topic. Aim for balance — every chapter should be backed by "
            f"≥3 source docs."
        ),
    )

    @field_validator("proposals")
    @classmethod
    def _validate_count(cls, v: list[ChapterProposal]) -> list[ChapterProposal]:
        if not (_PROPOSALS_MIN <= len(v) <= _PROPOSALS_MAX):
            raise ValueError(
                f"proposals count must be {_PROPOSALS_MIN}-"
                f"{_PROPOSALS_MAX}; got {len(v)}"
            )
        # Reject duplicate titles (case-insensitive).
        seen: set[str] = set()
        for p in v:
            k = p.title.casefold()
            if k in seen:
                raise ValueError(
                    f"duplicate chapter title (case-insensitive): "
                    f"{p.title!r}"
                )
            seen.add(k)
        return v


# -------------------------------------------------------------- #
# Structural seeding                                              #
# -------------------------------------------------------------- #
_H2_RE = re.compile(r"(?m)^\s{0,3}#{1,2}\s+(.+?)$")


def _extract_h12_headings(body: str, max_n: int) -> list[str]:
    """First N H1/H2 headings from a markdown body."""
    out: list[str] = []
    for m in _H2_RE.finditer(body or ""):
        h = " ".join(m.group(1).strip().split())
        # Skip generic / boilerplate headings.
        if h.casefold() in {
            "introduction", "overview", "summary", "conclusion",
            "getting started", "about", "preface", "epilogue",
        }:
            continue
        out.append(h)
        if len(out) >= max_n:
            break
    return out


def _namespace_from_key(source_key: str) -> Optional[str]:
    """Extract a 'namespace' from a source key. Captures CLI subcommand
    patterns + file-tree top-level directories under `commands/`."""
    m = _CLI_PATTERN_RE.search(source_key)
    if m:
        return m.group(1).lower()
    # Fallback: file-tree top-level directory after `ingestion/{slug}/`.
    parts = source_key.split("/")
    if len(parts) >= 4 and parts[0] == "ingestion":
        # e.g. ingestion/claude-code/pages/0012-foo.md → "pages"
        # Less useful but at least signals structure.
        return parts[2].lower()
    return None


def extract_structural_seeds(
    *,
    source_keys: list[str],
    bodies_by_key: dict[str, str],
) -> dict:
    """Extract structural seeds from the corpus:
      - top-level H1/H2 headings (deduped, capped)
      - file-tree namespaces (CLI subcommands, doc sections)
    """
    headings_counter: Counter[str] = Counter()
    for key in source_keys:
        body = bodies_by_key.get(key) or ""
        for h in _extract_h12_headings(body, max_n=4):
            headings_counter[h] += 1

    namespaces_counter: Counter[str] = Counter()
    for key in source_keys:
        ns = _namespace_from_key(key)
        if ns:
            namespaces_counter[ns] += 1

    # Filter rare headings (appear in only 1 doc) — too narrow to be a
    # chapter seed. Keep ones that occur ≥2.
    seed_headings = [
        h for h, n in headings_counter.most_common(_SEED_MAX_HEADINGS) if n >= 2
    ][:_SEED_MAX_HEADINGS]
    seed_namespaces = [
        ns for ns, n in namespaces_counter.most_common(_SEED_MAX_NAMESPACES)
        if n >= 2
    ]

    return {
        "headings":   seed_headings,
        "namespaces": seed_namespaces,
    }


# -------------------------------------------------------------- #
# Prompt construction                                             #
# -------------------------------------------------------------- #
def _render_distillates_block(
    distillates: dict[str, dict], source_keys: list[str],
) -> str:
    """Render per-doc distillates (summary + key_terms) into a token-tight
    block for the proposer prompt."""
    lines: list[str] = []
    for i, key in enumerate(source_keys, 1):
        d = distillates.get(key) or {}
        summary = (d.get("summary") or "").strip()
        terms = d.get("key_terms") or []
        if not summary:
            continue
        terms_str = ", ".join(terms[:8])
        lines.append(f"[{i}] {key}\n    summary: {summary}\n    terms: {terms_str}")
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
    """Build the proposer prompt. When `distillates` is None, the proposer
    sees full doc bodies (pass-through path for small N). Otherwise it
    sees summaries + key_terms.

    `target_chapters` is the adaptive per-corpus target (sized to doc count)
    that steers the proposer away from under-chaptering large corpora."""
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

    return (
        f"You are the Chapter Planner for the {framework} documentation.\n\n"
        f"Your job: propose a balanced set of about {target_chapters} chapters "
        f"(TARGET={target_chapters}, sized to this corpus of "
        f"{len(source_keys)} docs; stay close to it, hard range "
        f"{_PROPOSALS_MIN}-{_PROPOSALS_MAX}) that COVER THE FULL SURFACE AREA "
        f"of this framework. Too FEW chapters forces unrelated topics to share "
        f"one over-broad chapter; aim for ~{target_chapters} so each chapter is "
        f"a cohesive, single-topic unit. Each chapter must:\n"
        f"  - have a concrete, specific title ({_TITLE_MIN_WORDS}-"
        f"{_TITLE_MAX_WORDS} words; no generic 'Introduction'/"
        f"'Overview'/'Conclusion')\n"
        f"  - cover a DISTINCT topic from every other chapter\n"
        f"  - be backed by ≥3 docs from the corpus\n"
        f"  - list {_CONCEPTS_MIN}-{_CONCEPTS_MAX} specific concepts/"
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
        f"1. Between {_PROPOSALS_MIN} and {_PROPOSALS_MAX} chapters.\n"
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
        lines.append("  Titles: " + ", ".join(titles[:_PROPOSALS_MAX]))
    block = "\n".join(lines)
    return (
        f"You are the Universal Self-Consistency picker. Pick the BEST "
        f"of {len(candidates_summary)} chapter proposal sets for "
        f"{framework}.\n\n"
        f"== CANDIDATES ==\n{block}\n\n"
        f"Pick by:\n"
        f"  1. Balance (no single chapter dominates concepts)\n"
        f"  2. Coverage (more chapters = more coverage, up to {_PROPOSALS_MAX})\n"
        f"  3. Specificity (concrete titles, not 'Overview'/'Introduction')\n\n"
        f"OUTPUT — STRICT JSON:\n"
        f'{{"chosen_index": <int>, "reason": "<short>"}}'
    )


def summarize_proposal(props: list[ChapterProposal]) -> dict:
    """Compact summary for the USC vote picker."""
    return {
        "n_chapters":         len(props),
        "titles":             [p.title for p in props],
        "max_concept_count":  max((len(p.key_concepts) for p in props), default=0),
        "total_concepts":     sum(len(p.key_concepts) for p in props),
    }
