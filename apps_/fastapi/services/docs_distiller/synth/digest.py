"""digest_construct — LLM-assigned source-to-section routing library.

Pure module: Pydantic schemas + prompt templates + deterministic
aggregation. No I/O, no LLM calls — that lives in
`synth/nodes/digest_construct.py`.

ARCHITECTURE — novel adaptation, May 2026 SOTA

Borrows two patterns from current literature and combines them:

  - PER-SOURCE LLM CALL (LLMxMapReduce-V3 arXiv 2510.10890 §3.2):
    "for each reference document, the system prompts an LLM to
    generate a brief summary along with suggestions for improving the
    current outline." We use the per-source LLM-call shape but apply
    it to a DIFFERENT problem (source-to-section routing), not the
    skeleton-refine problem the paper targets.

  - STRUCTURED PAPER CARD (IterSurvey arXiv 2510.21900):
    "Paper cards distill each paper into its contributions, methods,
    and findings." Same per-paper distillation pattern, tighter
    schema. Our `SourceDigest` is the technical-docs analog: one
    digest per source page, distilled into structured per-section
    contributions.

WHY THIS REPLACES THE DEPRECATED PHASE B (cosine routing)

Deprecated Phase B:
  - Embed (heading + goal) per section
  - Embed (prev_heading + code_signature) per vault hash
  - argmax(cosine) → assign hash to one section
  - Failure mode: ch02 content routed to ch04 (semantic neighbors
    confused by 120-char hash signatures)

New digest_construct:
  - Per source: 1 LLM call sees the FULL source + full outline
  - LLM reasons WHICH section(s) the source contributes to AND WHAT
    specifically, with typed relevance levels (primary/supporting/
    tangential)
  - LLM also routes vault sentinels to sections (replaces argmax)
  - Aggregate: deterministic invert → per-section table + coverage
    stats. NO second LLM consolidation pass (the per-source digests
    are already structured; deterministic merge is sufficient)

INPUT / OUTPUT

  Input (per source page):
    outline_compact          — list[{section_id, heading, description}]
    source_key               — MinIO key
    source_md                — full normalized + sentinelized markdown
    source_vault_hashes      — list[str] of 12-hex hashes present
                                (so the LLM only routes hashes it
                                ACTUALLY sees, not hallucinated ones)

  Output per source (Pydantic-validated):
    SourceDigest{
      source_key, source_title, overall_summary,
      contributes_to: [SectionContribution],
      unassigned_code_refs: [str]
    }

  Output for whole chapter (post-aggregate):
    ChapterDigest{
      chapter_id, framework_slug,
      per_source: list[SourceDigest],
      per_section: dict[section_id, list[SectionContribution]],
      coverage_stats: CoverageStats
    }

DOWNSTREAM CONSUMERS

  - `sawc_write` reads `per_section` to know which sources contribute
    what to each section. Section drafting is grounded on these
    digests (not on full source markdown).
  - `checklist_eval` reads `coverage_stats` to enforce per-section
    minimums (e.g. ≥1 primary source per section).
  - `mgsr_replan` reads coverage flags to emit replan actions when
    sections are empty or sources over-spread.

TUNABLES

  _CONTRIB_RELEVANCE_LEVELS    — Literal["primary","supporting","tangential"]
  _MAX_KEY_FACTS_PER_CONTRIB   = 5  (LLM-side hard cap; >5 = vague)
  _MIN_KEY_FACTS_PER_CONTRIB   = 1
  _MAX_CONTRIBS_PER_SOURCE     = 20 (one source rarely useful to >20)
  _OVER_SPREAD_THRESHOLD       = 3  (claim primary in >3 sections = sus)
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Versioning + tunables
# =============================================================================
DIGEST_SCHEMA_VERSION = "1.0"
DIGEST_PROMPT_VERSION = "v1-2026-05-19"

_MAX_KEY_FACTS_PER_CONTRIB = 5
_MIN_KEY_FACTS_PER_CONTRIB = 1
_MAX_CONTRIBS_PER_SOURCE = 20
_OVER_SPREAD_THRESHOLD = 3
_SUMMARY_MIN_CHARS = 20
_SUMMARY_MAX_CHARS = 600
_KEY_FACT_MIN_CHARS = 6
_KEY_FACT_MAX_CHARS = 300
_OVERALL_SUMMARY_MIN_CHARS = 30
_OVERALL_SUMMARY_MAX_CHARS = 800
_SOURCE_TITLE_MIN_CHARS = 3
_SOURCE_TITLE_MAX_CHARS = 200

# 12-hex hash format (matches vault.py sentinels)
_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")

Relevance = Literal["primary", "supporting", "tangential"]


# =============================================================================
# Pydantic schemas — LLM output side
# =============================================================================
class SectionContribution(BaseModel):
    """One source's contribution to ONE outline section."""
    section_id: str = Field(
        description=(
            "Outline section id this contribution targets. MUST be one "
            "of the section_ids listed in the prompt outline (s1..sN)."
        ),
    )
    relevance: Relevance = Field(
        description=(
            "How central this source is to the section: "
            "'primary' = source is a main authority for the section's "
            "content; 'supporting' = useful detail but not the main "
            "reference; 'tangential' = mentions in passing."
        ),
    )
    summary: str = Field(
        description=(
            "1-3 sentences (20-600 chars) summarizing exactly what THIS "
            "source contributes to THIS section. Concrete, no vague "
            "phrases. Used by sawc_write as the grounded teaching "
            "material for the section."
        ),
    )
    key_facts: list[str] = Field(
        description=(
            "1-5 concrete extractable claims (6-300 chars each), one "
            "per line. Each fact should be standalone (no 'see above'). "
            "Examples: 'CountryAlpha2 inherits from str', 'Luhn check "
            "uses mod-10 weighted sum'. Used by sawc_write to ground "
            "claims with citations."
        ),
    )
    code_refs: list[str] = Field(
        default_factory=list,
        description=(
            "Vault hashes from THIS source that belong to THIS section. "
            "Each entry must be a 16-hex string matching a hash listed "
            "in the prompt's `vault_hashes_in_source`. Empty list if "
            "the section's contribution is prose-only or no code refs "
            "from this source belong here."
        ),
    )

    @field_validator("section_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _SECTION_ID_RE.match(v):
            raise ValueError(
                f"section_id {v!r} must match /^s\\d+$/ (e.g. 's1')"
            )
        return v

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_SUMMARY_MIN_CHARS <= len(s) <= _SUMMARY_MAX_CHARS):
            raise ValueError(
                f"summary must be {_SUMMARY_MIN_CHARS}-"
                f"{_SUMMARY_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("key_facts")
    @classmethod
    def _validate_facts(cls, v: list[str]) -> list[str]:
        if not (_MIN_KEY_FACTS_PER_CONTRIB <= len(v)
                <= _MAX_KEY_FACTS_PER_CONTRIB):
            raise ValueError(
                f"key_facts count must be "
                f"{_MIN_KEY_FACTS_PER_CONTRIB}-"
                f"{_MAX_KEY_FACTS_PER_CONTRIB}; got {len(v)}"
            )
        cleaned: list[str] = []
        for f in v:
            s = " ".join(f.strip().split())
            if not (_KEY_FACT_MIN_CHARS <= len(s) <= _KEY_FACT_MAX_CHARS):
                raise ValueError(
                    f"key_fact length must be {_KEY_FACT_MIN_CHARS}-"
                    f"{_KEY_FACT_MAX_CHARS} chars; got {len(s)} "
                    f"for {f!r}"
                )
            cleaned.append(s)
        return cleaned

    @field_validator("code_refs")
    @classmethod
    def _validate_refs(cls, v: list[str]) -> list[str]:
        for h in v:
            if not _HASH_RE.match(h):
                raise ValueError(
                    f"code_ref {h!r} must be 16 lowercase hex chars"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate code_refs in same contribution: {v}")
        return v


class _LLMDigestPayload(BaseModel):
    """What the LLM returns. The source_key is injected by the node
    code (we know it; LLM doesn't need to echo it). Other fields are
    pure LLM output."""
    source_title: str = Field(
        description=(
            "A concise title for this source page (3-200 chars). "
            "Derive from the markdown's first H1 if present, or "
            "synthesize from the URL slug. Used by the UI when "
            "displaying digests."
        ),
    )
    overall_summary: str = Field(
        description=(
            "1-paragraph overall summary of what THIS source is about "
            "(30-800 chars). NOT keyed to any section — just the "
            "source's identity. Used by mgsr_replan and downstream "
            "audit to verify source identity."
        ),
    )
    contributes_to: list[SectionContribution] = Field(
        description=(
            "List of contributions, one per outline section this source "
            "ACTUALLY contributes to. Omit sections this source doesn't "
            "touch — don't pad with empty/'not applicable' entries. "
            "0-20 entries. A single source rarely contributes to >5 "
            "sections; many will only contribute to 1-3."
        ),
    )
    unassigned_code_refs: list[str] = Field(
        default_factory=list,
        description=(
            "Vault hashes present in this source that you couldn't "
            "confidently route to a specific section. Each entry must "
            "be a 16-hex hash from `vault_hashes_in_source`. Empty "
            "list if every present hash got routed."
        ),
    )

    @field_validator("source_title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_SOURCE_TITLE_MIN_CHARS <= len(s) <= _SOURCE_TITLE_MAX_CHARS):
            raise ValueError(
                f"source_title length must be {_SOURCE_TITLE_MIN_CHARS}-"
                f"{_SOURCE_TITLE_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("overall_summary")
    @classmethod
    def _validate_overall(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_OVERALL_SUMMARY_MIN_CHARS <= len(s)
                <= _OVERALL_SUMMARY_MAX_CHARS):
            raise ValueError(
                f"overall_summary length must be "
                f"{_OVERALL_SUMMARY_MIN_CHARS}-{_OVERALL_SUMMARY_MAX_CHARS} "
                f"chars; got {len(s)}"
            )
        return s

    @field_validator("contributes_to")
    @classmethod
    def _validate_contribs(
        cls, v: list[SectionContribution],
    ) -> list[SectionContribution]:
        if len(v) > _MAX_CONTRIBS_PER_SOURCE:
            raise ValueError(
                f"contributes_to has {len(v)} entries; max "
                f"{_MAX_CONTRIBS_PER_SOURCE} allowed (one source "
                f"is rarely useful in more sections than that)"
            )
        ids = [c.section_id for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError(
                f"duplicate section_id in contributes_to: {ids}"
            )
        return v

    @field_validator("unassigned_code_refs")
    @classmethod
    def _validate_unassigned(cls, v: list[str]) -> list[str]:
        for h in v:
            if not _HASH_RE.match(h):
                raise ValueError(
                    f"unassigned code_ref {h!r} must be 16 hex chars"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate unassigned_code_refs: {v}")
        return v


# =============================================================================
# Pydantic schemas — persisted side (LLM output + node-injected fields)
# =============================================================================
class SourceDigest(BaseModel):
    """A single source's digest. Persisted in the chapter digest blob.

    Mirrors `_LLMDigestPayload` but adds node-injected `source_key`
    (we know it; the LLM doesn't need to echo it) and observability
    fields (`deployment`, `wall_ms`).
    """
    source_key: str
    source_title: str
    overall_summary: str
    contributes_to: list[SectionContribution]
    unassigned_code_refs: list[str] = Field(default_factory=list)
    deployment: Optional[str] = None
    wall_ms: Optional[int] = None


class CoverageStats(BaseModel):
    """Aggregate coverage metrics over the chapter digest.

    Drives:
      - `checklist_eval` per-section minimums
      - `mgsr_replan` replan actions (empty section → merge/delete)
      - UI KPI badge (`src=N · cov=M/N · orph=K`)
    """
    n_sources:                int
    n_sections:               int
    sections_with_primary:    int   # count of sections with ≥1 primary contributor
    empty_sections:           list[str]   # section_ids with 0 contributions
    over_spread_sources:      list[str]   # source_keys claiming primary in >threshold sections
    orphan_code_refs:         int          # vault hashes no section claimed
    avg_sources_per_section:  float
    avg_sections_per_source:  float


class ChapterDigest(BaseModel):
    """Full chapter digest — what gets persisted to MinIO."""
    schema_version: str = DIGEST_SCHEMA_VERSION
    prompt_version: str = DIGEST_PROMPT_VERSION
    chapter_id:     str
    chapter_title:  str
    framework_slug: str
    n_pydantic_fail: int = 0
    per_source: list[SourceDigest]
    per_section: dict[str, list[SectionContribution]]
    coverage_stats: CoverageStats


# =============================================================================
# Deterministic aggregation
# =============================================================================
def build_per_section_index(
    per_source: list[SourceDigest],
    section_ids: list[str],
) -> dict[str, list[SectionContribution]]:
    """Invert per-source contributions → per-section list.

    `section_ids` is the canonical list from the outline; sections with
    zero contributions still appear as empty lists (so checklist_eval
    can detect missing coverage easily).

    Within each section, contributions are sorted by relevance
    (primary → supporting → tangential), then by source_key for
    stable ordering.
    """
    _RELEVANCE_ORDER = {"primary": 0, "supporting": 1, "tangential": 2}

    per_section: dict[str, list[SectionContribution]] = {
        sid: [] for sid in section_ids
    }
    for src in per_source:
        for contrib in src.contributes_to:
            if contrib.section_id in per_section:
                per_section[contrib.section_id].append(contrib)
            # silently drop contributions to unknown section_ids;
            # validate_source_digest catches this for the LLM to repair

    # Stable sort within each section
    for sid in per_section:
        per_section[sid].sort(
            key=lambda c: (_RELEVANCE_ORDER.get(c.relevance, 9), id(c))
        )
    return per_section


def compute_coverage_stats(
    per_source: list[SourceDigest],
    per_section: dict[str, list[SectionContribution]],
    section_ids: list[str],
    all_vault_hashes: list[str],
) -> CoverageStats:
    """Compute coverage metrics for downstream consumers.

    `all_vault_hashes` is the union of every hash mentioned in any
    source — used to identify orphans (hashes no contribution claims).
    """
    n_sources = len(per_source)
    n_sections = len(section_ids)

    sections_with_primary = sum(
        1 for sid in section_ids
        if any(c.relevance == "primary" for c in per_section.get(sid, []))
    )

    empty_sections = [
        sid for sid in section_ids
        if not per_section.get(sid)
    ]

    # Over-spread sources: claim "primary" in too many sections (suggests
    # the LLM hallucinated relevance or the source is genuinely broad —
    # mgsr_replan can decide whether to merge sections or accept it)
    over_spread_sources: list[str] = []
    for src in per_source:
        n_primary = sum(
            1 for c in src.contributes_to if c.relevance == "primary"
        )
        if n_primary > _OVER_SPREAD_THRESHOLD:
            over_spread_sources.append(src.source_key)

    # Orphan code_refs: hashes present in some source but routed to no
    # section by anyone (whether unassigned by that source OR omitted
    # entirely from another source's contribs)
    claimed_hashes: set[str] = set()
    for sid, contribs in per_section.items():
        for c in contribs:
            claimed_hashes.update(c.code_refs)
    all_hashes_set = set(all_vault_hashes)
    orphan_code_refs = len(all_hashes_set - claimed_hashes)

    # Avg fan-out metrics
    total_contribs = sum(len(s.contributes_to) for s in per_source)
    avg_sources_per_section = (
        sum(len(per_section.get(sid, [])) for sid in section_ids) / n_sections
        if n_sections else 0.0
    )
    avg_sections_per_source = (
        total_contribs / n_sources if n_sources else 0.0
    )

    return CoverageStats(
        n_sources=n_sources,
        n_sections=n_sections,
        sections_with_primary=sections_with_primary,
        empty_sections=empty_sections,
        over_spread_sources=over_spread_sources,
        orphan_code_refs=orphan_code_refs,
        avg_sources_per_section=avg_sources_per_section,
        avg_sections_per_source=avg_sections_per_source,
    )


def validate_source_digest(
    payload: _LLMDigestPayload,
    *,
    valid_section_ids: set[str],
    valid_vault_hashes: set[str],
) -> list[str]:
    """Cross-reference validator beyond per-field schema rules.

    Returns a list of natural-language issue strings suitable for
    feeding back to the LLM as repair instructions. Empty list = clean.

    Pydantic already enforces format-level rules (section_id regex,
    hash regex, length bounds, duplicate detection). This catches
    CROSS-source/outline invariants:

      - section_ids must EXIST in the outline (not just match the regex)
      - code_refs must EXIST in this source's vault_hashes (LLM
        sometimes hallucinates hash values)
      - unassigned_code_refs must also be in vault_hashes
      - a code_ref cannot appear in BOTH a contribution AND unassigned
    """
    issues: list[str] = []
    bad_section_ids: set[str] = set()
    bad_code_refs_per_contrib: dict[str, list[str]] = {}
    contrib_hash_to_section: dict[str, str] = {}

    for c in payload.contributes_to:
        if c.section_id not in valid_section_ids:
            bad_section_ids.add(c.section_id)
        bad = [h for h in c.code_refs if h not in valid_vault_hashes]
        if bad:
            bad_code_refs_per_contrib[c.section_id] = bad
        for h in c.code_refs:
            if h in contrib_hash_to_section:
                issues.append(
                    f"vault hash {h!r} routed to both section "
                    f"{contrib_hash_to_section[h]!r} and "
                    f"{c.section_id!r} — a hash can only belong to ONE "
                    f"section. Drop the less-confident assignment."
                )
            else:
                contrib_hash_to_section[h] = c.section_id

    if bad_section_ids:
        issues.append(
            f"contributions reference unknown section_ids: "
            f"{sorted(bad_section_ids)}. Use ONLY ids from the outline "
            f"(s1..sN as listed in the prompt)."
        )
    for sid, bad in bad_code_refs_per_contrib.items():
        issues.append(
            f"section {sid!r}: code_refs {bad} are not in this source's "
            f"vault_hashes — only assign hashes present in the source."
        )

    bad_unassigned = [
        h for h in payload.unassigned_code_refs
        if h not in valid_vault_hashes
    ]
    if bad_unassigned:
        issues.append(
            f"unassigned_code_refs {bad_unassigned} are not in this "
            f"source's vault_hashes."
        )

    overlap = set(payload.unassigned_code_refs) & set(
        contrib_hash_to_section.keys()
    )
    if overlap:
        issues.append(
            f"vault hashes {sorted(overlap)} appear BOTH in a "
            f"contribution AND in unassigned_code_refs — pick one. "
            f"If you routed it, drop from unassigned. If unsure, drop "
            f"from contribution."
        )

    return issues


# =============================================================================
# Prompt templates
# =============================================================================
def _format_outline_compact(outline_sections: list[dict]) -> str:
    """Format outline as a compact block for the digest prompt.

    Each entry: `[s1] Heading — description`. Skips DAG/prereqs (the
    digest LLM only needs to know WHICH sections exist + their topic);
    DAG was already used by outline_sdp and is preserved separately.
    """
    lines: list[str] = []
    for s in outline_sections:
        lines.append(
            f"[{s.get('section_id', '?')}] {s.get('heading', '?')} — "
            f"{s.get('description', '?')}"
        )
    return "\n".join(lines)


def build_digest_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    outline_sections: list[dict],
    source_key: str,
    source_md: str,
    source_vault_hashes: list[str],
) -> str:
    """Build the per-source digest prompt.

    The LLM sees ONE source at a time + the FULL outline. It decides
    which sections this source contributes to (multi-label, with a
    relevance grade per assignment) and routes vault sentinels.
    """
    outline_block = _format_outline_compact(outline_sections)
    hash_block = (
        "\n".join(f"  - {h}" for h in source_vault_hashes)
        if source_vault_hashes
        else "  (none — this source has no fenced code blocks)"
    )
    return (
        f"You are the Digest Constructor — step 4 of the Docs Distiller "
        f"synth pipeline. The chapter outline has already been "
        f"decomposed by outline_sdp (step 3). Your job is to read ONE "
        f"source page and decide which sections it contributes to + "
        f"WHAT specifically.\n\n"

        f"This is per-source LLM-assigned routing (replaces deprecated "
        f"Phase B cosine routing). Borrows the per-source-digest "
        f"pattern from LLMxMapReduce-V3 (arXiv 2510.10890) + the typed "
        f"paper-card schema from IterSurvey (arXiv 2510.21900). Your "
        f"output drives sawc_write's section drafting downstream.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n\n"

        f"== OUTLINE SECTIONS (each is `[id] heading — description`) ==\n"
        f"{outline_block}\n"
        f"== END OUTLINE ==\n\n"

        f"== SOURCE: {source_key} ==\n"
        f"VAULT HASHES PRESENT IN THIS SOURCE "
        f"({len(source_vault_hashes)}):\n"
        f"{hash_block}\n\n"

        f"SOURCE MARKDOWN:\n"
        f"{source_md}\n"
        f"== END SOURCE ==\n\n"

        f"== OUTPUT — strict JSON matching this schema ==\n"
        f"{{\n"
        f'  "source_title": "3-200 chars",\n'
        f'  "overall_summary": "1-paragraph what this source is about (30-800 chars)",\n'
        f'  "contributes_to": [\n'
        f'    {{\n'
        f'      "section_id":  "s1",     /* MUST be one of the outline ids above */\n'
        f'      "relevance":   "primary" | "supporting" | "tangential",\n'
        f'      "summary":     "1-3 sentences (20-600 chars) — what THIS source contributes to THIS section",\n'
        f'      "key_facts":   ["fact 1", ...],  /* 1-5 concrete claims, 6-300 chars each */\n'
        f'      "code_refs":   ["12-hex", ...]   /* vault hashes from THIS source that BELONG to this section; subset of vault_hashes_in_source above */\n'
        f'    }},\n'
        f'    ... 0-20 entries ...\n'
        f'  ],\n'
        f'  "unassigned_code_refs": ["12-hex", ...]   /* vault hashes you couldn\'t confidently route */\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. section_id MUST be one of the outline ids above. Inventing "
        f"   ids like 's99' or 's_intro' is a hard violation.\n"
        f"2. OMIT sections this source doesn't actually contribute to. "
        f"   Do NOT pad with empty 'not applicable' contributions.\n"
        f"3. relevance must be HONEST: 'primary' = this source is the "
        f"   main authority for that section; 'supporting' = useful "
        f"   detail but not the lead reference; 'tangential' = mentions "
        f"   in passing. A source claiming 'primary' in >3 sections will "
        f"   be flagged as over-spread (likely hallucinated).\n"
        f"4. code_refs MUST be 16-hex strings from `vault_hashes_in_source` "
        f"   above. Routing a hash that isn't in that list is a hard "
        f"   violation (you can't see hashes that aren't here).\n"
        f"5. A single vault hash can appear in AT MOST ONE contribution. "
        f"   If you're unsure, put it in `unassigned_code_refs`.\n"
        f"6. A vault hash cannot appear in BOTH contributes_to AND "
        f"   unassigned_code_refs.\n"
        f"7. summary should be CONCRETE — name actual APIs / types / "
        f"   methods. Avoid 'demonstrates various patterns' or 'discusses "
        f"   concepts'.\n"
        f"8. key_facts are STANDALONE claims — no 'see above' references. "
        f"   Each fact should be verifiable from the source.\n\n"

        f"== DECOMPOSITION GUIDANCE ==\n"
        f"- Most sources contribute to 1-3 sections, not all of them. "
        f"Be selective.\n"
        f"- If the source is broadly about ONE section's topic, that "
        f"section gets 'primary' and others may not appear at all.\n"
        f"- If the source is a reference / catalog covering multiple "
        f"sections, expect 3-5 'supporting' contributions.\n"
        f"- 'tangential' should be rare — only when the source briefly "
        f"references a concept relevant to a section.\n\n"

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation."
    )


def build_repair_prompt(
    *,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    outline_sections: list[dict],
    source_key: str,
    source_md: str,
    source_vault_hashes: list[str],
    current_json: str,
    issues: list[str],
) -> str:
    """Repair prompt — given an LLM digest that failed validation, ask
    for a fixed version with the SAME schema. Issues are
    machine-readable strings produced by `validate_source_digest`."""
    outline_block = _format_outline_compact(outline_sections)
    hash_block = (
        "\n".join(f"  - {h}" for h in source_vault_hashes)
        if source_vault_hashes
        else "  (none)"
    )
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this source digest. Keep the same "
        f"JSON schema (source_title + overall_summary + contributes_to "
        f"+ unassigned_code_refs). Preserve good fields; only change "
        f"what's needed to clear the issues below.\n\n"

        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"FRAMEWORK: {framework}\n"
        f"SOURCE: {source_key}\n\n"

        f"OUTLINE SECTIONS (use ONLY these section_ids):\n"
        f"{outline_block}\n\n"

        f"VAULT HASHES PRESENT IN THIS SOURCE (use ONLY these for code_refs):\n"
        f"{hash_block}\n\n"

        f"SOURCE MARKDOWN (for context):\n"
        f"{source_md}\n\n"

        f"CURRENT DIGEST:\n{current_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"Respond ONLY with valid JSON matching the original schema. "
        f"NO commentary, NO markdown wrapping."
    )


# =============================================================================
# Helpers
# =============================================================================
_VAULT_HASH_IN_TEXT_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"\s*/>')


def extract_vault_hashes(md_text: str) -> list[str]:
    """Find every vault sentinel `<code-ref hash="..."/>` in `md_text`
    and return the unique 16-hex hashes in order of first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _VAULT_HASH_IN_TEXT_RE.finditer(md_text or ""):
        h = m.group(1)
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def derive_source_title_fallback(md_text: str, source_key: str) -> str:
    """If the LLM-emitted source_title is unusable, derive one from the
    markdown's first H1 OR from the source_key filename."""
    if md_text:
        m = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
        if m:
            title = m.group(1).strip().strip("#").strip()
            if 3 <= len(title) <= 200:
                return title
    # Fallback: derive from filename
    base = source_key.rsplit("/", 1)[-1] or source_key
    base = base.rsplit(".", 1)[0]
    # Strip leading 4-digit index pattern (e.g. "0022-foo")
    base = re.sub(r"^\d+-", "", base)
    title = " ".join(p.capitalize() for p in base.split("-")[:8])
    return title or source_key
