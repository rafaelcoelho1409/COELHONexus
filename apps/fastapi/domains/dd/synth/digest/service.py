"""digest_construct service — all functions."""
from __future__ import annotations

import re

from collections import defaultdict

from .constants import (
    _MAX_KEY_FACTS_PER_CONTRIB,
    _MERGE_CONTAINMENT,
    _MERGE_JACCARD,
    _MERGE_MIN_PRIMARY_TO_DEFEND,
    _OVER_SPREAD_THRESHOLD,
    _VAULT_HASH_IN_TEXT_RE,
)
from .types import (
    CoverageStats,
    SectionContribution,
    SourceDigest,
    _LLMDigestPayload,
)


_RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}


def _best_relevance(a: str, b: str) -> str:
    """Return the stronger of two relevance grades (primary > supporting
    > tangential)."""
    return a if _RELEVANCE_RANK.get(a, 9) <= _RELEVANCE_RANK.get(b, 9) else b


# =============================================================================
# Deterministic aggregation
# =============================================================================
def build_per_section_index(
    per_source: list[SourceDigest],
    section_ids: list[str],
) -> dict[str, list[SectionContribution]]:
    """Invert per-source contributions -> per-section list.

    `section_ids` is the canonical list from the outline; sections with
    zero contributions still appear as empty lists (so checklist_eval
    can detect missing coverage easily).

    Within each section, contributions are sorted by relevance
    (primary -> supporting -> tangential), then by source_key for
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


def _resolve_merge(merged: dict[str, str], sid: str) -> str:
    """Follow a merge chain loser→winner→... to the terminal winner."""
    seen: set[str] = set()
    while sid in merged and sid not in seen:
        seen.add(sid)
        sid = merged[sid]
    return sid


def merge_overlapping_sections(
    per_source: list[SourceDigest],
    outline_sections: list[dict],
    *,
    jaccard: float = _MERGE_JACCARD,
    containment: float = _MERGE_CONTAINMENT,
    min_primary_to_defend: int = _MERGE_MIN_PRIMARY_TO_DEFEND,
) -> tuple[list[SourceDigest], dict[str, str]]:
    """Fix #3 (DD-SYNTH-SECTION-COUNT, 2026-05-29 PM) — fold sections whose
    PRIMARY source pools overlap heavily into a single section.

    This is the definitive overlap signal the outline-time heading/embedding
    proxy cannot see: two sections with DIFFERENT headings (e.g. ch-13's
    "Cost Tracking" vs "OpenTelemetry Configuration") that nonetheless draw
    on the SAME 2-3 source documents are the same scope, and the writer will
    recycle the same code into both — which the renderer then strips into
    hollow "see other section" cross-references.

    Returns `(retagged_per_source, merged_map)` where `merged_map` is
    `{loser_section_id: winner_section_id}`. The returned per_source has each
    losing contribution re-tagged to its winner (deduped within each source,
    unioning code_refs/key_facts, keeping the stronger relevance), so
    rebuilding `build_per_section_index` over it yields the merged index with
    losers as empty lists. Pure + deterministic given identical input.

    Merge rule for a pair (BIG = more primaries, SMALL = fewer; ties broken
    by outline order so the EARLIER/foundational section wins):
      - Jaccard(primaries) >= `jaccard`  → strong mutual overlap, OR
      - containment(small ⊆ big) >= `containment` AND SMALL brings fewer
        than `min_primary_to_defend` primary sources the BIG one lacks
        (i.e. SMALL is not independently defensible).
    Sections with zero primaries are never a merge TARGET on their own but
    can be folded when subsumed (containment of an empty set is treated as
    1.0 with 0 unique). Conservative by design — render-time dedup (#1) is
    the safety net for residual overlap.
    """
    order = {
        s.get("section_id"): i for i, s in enumerate(outline_sections)
    }

    def pools(merged: dict[str, str]) -> dict[str, set[str]]:
        prim: dict[str, set[str]] = defaultdict(set)
        for src in per_source:
            for c in src.contributes_to:
                if c.relevance == "primary":
                    prim[_resolve_merge(merged, c.section_id)].add(
                        src.source_key
                    )
        return prim

    merged_map: dict[str, str] = {}
    # All section_ids that currently receive any contribution.
    live_ids = {
        _resolve_merge(merged_map, c.section_id)
        for src in per_source for c in src.contributes_to
    }

    while True:
        prim = pools(merged_map)
        live = sorted(
            (sid for sid in live_ids if sid not in merged_map),
            key=lambda s: order.get(s, 9_999),
        )
        chosen: tuple[str, str] | None = None
        for i in range(len(live)):
            for j in range(i + 1, len(live)):
                a, b = live[i], live[j]
                pa, pb = prim.get(a, set()), prim.get(b, set())
                if not pa and not pb:
                    continue
                # BIG = more primaries; tie → earlier outline order.
                if (len(pa), -order.get(a, 9_999)) >= (
                    len(pb), -order.get(b, 9_999)
                ):
                    big, small, pbig, psmall = a, b, pa, pb
                else:
                    big, small, pbig, psmall = b, a, pb, pa
                union = pbig | psmall
                inter = pbig & psmall
                jac = (len(inter) / len(union)) if union else 0.0
                contain = (len(inter) / len(psmall)) if psmall else 1.0
                unique_small = len(psmall - pbig)
                if jac >= jaccard or (
                    contain >= containment
                    and unique_small < min_primary_to_defend
                ):
                    chosen = (small, big)  # (loser, winner)
                    break
            if chosen:
                break
        if not chosen:
            break
        loser, winner = chosen
        merged_map[loser] = winner

    # Collapse any transitive chains so every loser maps to a terminal winner.
    merged_map = {
        loser: _resolve_merge(merged_map, winner)
        for loser, winner in merged_map.items()
    }
    if not merged_map:
        return per_source, {}

    # Re-tag per_source: every contribution to a loser now points to its
    # winner; dedup contributions that collide within a single source.
    retagged: list[SourceDigest] = []
    for src in per_source:
        by_sid: dict[str, SectionContribution] = {}
        for c in src.contributes_to:
            tgt = _resolve_merge(merged_map, c.section_id)
            if tgt == c.section_id and tgt not in merged_map.values():
                # Untouched section — keep as-is unless a collision occurs.
                pass
            existing = by_sid.get(tgt)
            if existing is None:
                by_sid[tgt] = (
                    c if c.section_id == tgt
                    else c.model_copy(update={"section_id": tgt})
                )
            else:
                by_sid[tgt] = existing.model_copy(update={
                    "section_id": tgt,
                    "relevance": _best_relevance(
                        existing.relevance, c.relevance
                    ),
                    "code_refs": list(dict.fromkeys(
                        existing.code_refs + c.code_refs
                    )),
                    "key_facts": list(dict.fromkeys(
                        existing.key_facts + c.key_facts
                    ))[:_MAX_KEY_FACTS_PER_CONTRIB],
                    "summary": existing.summary,
                })
        retagged.append(
            src.model_copy(update={"contributes_to": list(by_sid.values())})
        )
    return retagged, merged_map


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
        f"   in passing. Route each code example to the ONE section it "
        f"   most belongs in (DD-SYNTH-SECTION-RECYCLING-2026-05-29 #5). "
        f"   A source claiming 'primary' in >2 sections is flagged as "
        f"   over-spread — spreading the same code across sections forces "
        f"   the chapter to de-duplicate it into hollow cross-references. "
        f"   Pick the single best home; demote the rest to 'supporting' "
        f"   only when genuinely needed.\n"
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
        f"- Most sources contribute to 1-2 sections, not all of them. "
        f"Be selective — over-spreading is the #1 cause of recycled code.\n"
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
