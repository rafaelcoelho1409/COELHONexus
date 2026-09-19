"""digest_construct — pure domain logic (no I/O, no LLM, no async)."""
from __future__ import annotations
from . import params, patterns, schemas, versions

import json
import re
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from collections import defaultdict



_RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}


def _best_relevance(a: str, b: str) -> str:
    """Return stronger relevance grade (primary > supporting > tangential)."""
    return a if _RELEVANCE_RANK.get(a, 9) <= _RELEVANCE_RANK.get(b, 9) else b


def build_per_section_index(
    per_source: list[schemas.SourceDigest],
    section_ids: list[str],
) -> dict[str, list[schemas.SectionContribution]]:
    """Invert per-source contributions → per-section list; zero-contribution sections kept as empty lists."""
    _RELEVANCE_ORDER = {"primary": 0, "supporting": 1, "tangential": 2}

    per_section: dict[str, list[schemas.SectionContribution]] = {
        sid: [] for sid in section_ids
    }
    for src in per_source:
        for contrib in src.contributes_to:
            if contrib.section_id in per_section:
                per_section[contrib.section_id].append(contrib)
            # unknown section_ids silently dropped; validate_source_digest catches them for repair

    # Stable sort within each section
    for sid in per_section:
        per_section[sid].sort(
            key = lambda c: (_RELEVANCE_ORDER.get(c.relevance, 9), id(c))
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
    per_source: list[schemas.SourceDigest],
    outline_sections: list[dict],
    *,
    jaccard: float = params.MERGE_JACCARD,
    containment: float = params.MERGE_CONTAINMENT,
    min_primary_to_defend: int = params.MERGE_MIN_PRIMARY_TO_DEFEND,
) -> tuple[list[schemas.SourceDigest], dict[str, str]]:
    """Fold sections whose PRIMARY source pools overlap by Jaccard/containment; conservative — render dedup is the safety net."""
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
    live_ids = {
        _resolve_merge(merged_map, c.section_id)
        for src in per_source for c in src.contributes_to
    }

    while True:
        prim = pools(merged_map)
        live = sorted(
            (sid for sid in live_ids if sid not in merged_map),
            key = lambda s: order.get(s, 9_999),
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

    merged_map = {
        loser: _resolve_merge(merged_map, winner)
        for loser, winner in merged_map.items()
    }
    if not merged_map:
        return per_source, {}

    retagged: list[schemas.SourceDigest] = []
    for src in per_source:
        by_sid: dict[str, schemas.SectionContribution] = {}
        for c in src.contributes_to:
            tgt = _resolve_merge(merged_map, c.section_id)
            if tgt == c.section_id and tgt not in merged_map.values():
                # Untouched section — keep as-is unless a collision occurs.
                pass
            existing = by_sid.get(tgt)
            if existing is None:
                by_sid[tgt] = (
                    c if c.section_id == tgt
                    else c.model_copy(update = {"section_id": tgt})
                )
            else:
                by_sid[tgt] = existing.model_copy(update = {
                    "section_id": tgt,
                    "relevance": _best_relevance(
                        existing.relevance, c.relevance
                    ),
                    "code_refs": list(dict.fromkeys(
                        existing.code_refs + c.code_refs
                    )),
                    "key_facts": list(dict.fromkeys(
                        existing.key_facts + c.key_facts
                    ))[:params.MAX_KEY_FACTS_PER_CONTRIB],
                    "summary": existing.summary,
                })
        retagged.append(
            src.model_copy(update = {"contributes_to": list(by_sid.values())})
        )
    return retagged, merged_map


def compute_coverage_stats(
    per_source: list[schemas.SourceDigest],
    per_section: dict[str, list[schemas.SectionContribution]],
    section_ids: list[str],
    all_vault_hashes: list[str],
) -> schemas.CoverageStats:
    """Compute coverage metrics; all_vault_hashes identifies orphaned hashes."""
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

    over_spread_sources: list[str] = []
    for src in per_source:
        n_primary = sum(
            1 for c in src.contributes_to if c.relevance == "primary"
        )
        if n_primary > params.OVER_SPREAD_THRESHOLD:
            over_spread_sources.append(src.source_key)

    claimed_hashes: set[str] = set()
    for sid, contribs in per_section.items():
        for c in contribs:
            claimed_hashes.update(c.code_refs)
    all_hashes_set = set(all_vault_hashes)
    orphan_code_refs = len(all_hashes_set - claimed_hashes)

    total_contribs = sum(len(s.contributes_to) for s in per_source)
    avg_sources_per_section = (
        sum(len(per_section.get(sid, [])) for sid in section_ids) / n_sections
        if n_sections else 0.0
    )
    avg_sections_per_source = (
        total_contribs / n_sources if n_sources else 0.0
    )

    return schemas.CoverageStats(
        n_sources = n_sources,
        n_sections = n_sections,
        sections_with_primary = sections_with_primary,
        empty_sections = empty_sections,
        over_spread_sources = over_spread_sources,
        orphan_code_refs = orphan_code_refs,
        avg_sources_per_section = avg_sources_per_section,
        avg_sections_per_source = avg_sections_per_source,
    )


def validate_source_digest(
    payload: schemas.LLMDigestPayload,
    *,
    valid_section_ids: set[str],
    valid_vault_hashes: set[str],
) -> list[str]:
    """Cross-source/outline invariant validator; returns repair instructions for the LLM."""
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


def extract_vault_hashes(md_text: str) -> list[str]:
    """Return unique 16-hex vault sentinel hashes in order of first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for m in patterns.VAULT_HASH_IN_TEXT_RE.finditer(md_text or ""):
        h = m.group(1)
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def derive_source_title_fallback(md_text: str, source_key: str) -> str:
    """Derive title from first H1 or source_key filename when LLM-emitted title is unusable."""
    if md_text:
        m = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
        if m:
            title = m.group(1).strip().strip("#").strip()
            if 3 <= len(title) <= 200:
                return title
    base = source_key.rsplit("/", 1)[-1] or source_key
    base = base.rsplit(".", 1)[0]
    base = re.sub(r"^\d+-", "", base)
    title = " ".join(p.capitalize() for p in base.split("-")[:8])
    return title or source_key


_CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)


def is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same idiom as outline_sdp's classifier.
    The Rotator is a universal gateway with no context-length-aware arm
    filtering, so even a single 100K-char source can exceed a small
    -context arm from a heterogeneous multi-provider pool."""
    msg = str(e).lower()
    return any(marker in msg for marker in _CONTEXT_OVERFLOW_MARKERS)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_response(text: str) -> Optional[dict]:
    """Best-effort JSON extraction. Tolerates ```json fences + leading
    prose. Same approach as outline_sdp / planner.chapter_select."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix


def try_parse_payload(
    raw: dict,
) -> tuple[Optional[schemas.LLMDigestPayload], Optional[str]]:
    try:
        return schemas.LLMDigestPayload.model_validate(raw), None
    except ValidationError as e:
        return None, shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def compute_manifest_hash(
    *,
    outline_manifest_hash: str,
    source_keys: list[str],
    sources_bytes: int,
) -> str:
    payload = (
        f"outline = {outline_manifest_hash}|"
        f"sources = {','.join(sorted(source_keys))}|"
        f"n = {len(source_keys)}|"
        f"bytes = {sources_bytes}|"
        f"prompt = {versions.DIGEST_PROMPT_VERSION}|"
        f"schema = {versions.DIGEST_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_digest_payload(text: str) -> dict:
    """Parse the persisted digest blob."""
    return json.loads(text)
