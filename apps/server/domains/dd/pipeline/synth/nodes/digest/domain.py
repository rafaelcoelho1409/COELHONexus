"""digest_construct — pure domain logic (no I/O, no LLM, no async)."""
from __future__ import annotations
from . import params, patterns, schemas, versions

import json
import math
import re
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from collections import defaultdict


def _best_relevance(a: str, b: str) -> str:
    """Return stronger relevance grade (primary > supporting > tangential)."""
    return a if params.RELEVANCE_RANK.get(a, 9) <= params.RELEVANCE_RANK.get(b, 9) else b


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

    def blocks(merged: dict[str, str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for src in per_source:
            for c in src.contributes_to:
                out[_resolve_merge(merged, c.section_id)].update(c.code_refs)
        return out

    while True:
        prim = pools(merged_map)
        blk = blocks(merged_map)
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
                if (jac >= jaccard or (
                    contain >= containment
                    and unique_small < min_primary_to_defend
                )) and len(blk.get(big, set()) | blk.get(small, set())) <= params.MERGE_MAX_BLOCKS:
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


def _words(text: str) -> set[str]:
    return set(patterns.WORD_RE.findall((text or "").lower()))


def split_source_by_blocks(md_text: str, max_blocks: int) -> list[str]:
    """A page with more than `max_blocks` code blocks is cut into about equal chunks of whole blocks (each within a third of the target size). A cut falls on the last heading in the
    third of a chunk before the ideal cut (else on a blank line between two blocks), so a chunk keeps a topic's prose with its code; later chunks start with the page's own title
    line. A page with fewer blocks comes back whole."""
    hashes = extract_vault_hashes(md_text)
    if max_blocks < 1 or len(hashes) <= max_blocks:
        return [md_text]
    first_pos: dict[str, int] = {}
    for m in patterns.VAULT_HASH_IN_TEXT_RE.finditer(md_text):
        first_pos.setdefault(m.group(1), m.start())
    k = math.ceil(len(hashes) / max_blocks)
    per = math.ceil(len(hashes) / k)
    slack = per // 3
    cuts: list[int] = []
    for i in range(1, k):
        idx = i * per
        if idx >= len(hashes):
            break
        lo, hi = first_pos[hashes[idx - 1]], first_pos[hashes[idx]]
        lo_wide = first_pos[hashes[max(idx - slack, 1) - 1]]
        window = md_text[lo:hi]
        heads = [lo_wide + m.start() for m in patterns.HEADING_LINE_RE.finditer(md_text[lo_wide:hi])]
        if heads:
            cut = heads[-1]
        elif "\n\n" in window:
            cut = lo + window.rfind("\n\n") + 2
        else:
            cut = md_text.rfind("\n", lo, hi) + 1
        if cut <= (cuts[-1] if cuts else 0):
            continue
        cuts.append(cut)
    title = next((ln for ln in md_text.splitlines() if ln.startswith("# ")), "")
    bounds = [0] + cuts + [len(md_text)]
    chunks = [md_text[a:b] for a, b in zip(bounds, bounds[1:]) if md_text[a:b].strip()]
    return [chunks[0]] + [f"{title} (continued)\n\n{c}" if title else c for c in chunks[1:]]


def merge_chunk_digests(source_key: str, parts: list[schemas.SourceDigest]) -> schemas.SourceDigest:
    """One SourceDigest from the digests of a page's chunks: contributions to the same section are merged (code refs and key facts united, the better relevance kept), and a block
    one chunk could not place stays unassigned only if no other chunk placed it."""
    first = parts[0]
    merged: dict[str, schemas.SectionContribution] = {}
    for part in parts:
        for c in part.contributes_to:
            cur = merged.get(c.section_id)
            if cur is None:
                merged[c.section_id] = c.model_copy(deep = True)
                continue
            rel = min(cur.relevance, c.relevance, key = lambda r: params.RELEVANCE_RANK.get(r, 9))
            merged[c.section_id] = cur.model_copy(update = {
                "code_refs": list(dict.fromkeys(list(cur.code_refs) + list(c.code_refs))),
                "key_facts": list(dict.fromkeys(list(cur.key_facts) + list(c.key_facts)))[:params.MAX_KEY_FACTS_PER_CONTRIB],
                "relevance": rel,
            })
    routed = {h for c in merged.values() for h in c.code_refs}
    unassigned = list(dict.fromkeys(h for p in parts for h in p.unassigned_code_refs if h not in routed))
    return schemas.SourceDigest(
        source_key = source_key, source_title = first.source_title, overall_summary = first.overall_summary,
        contributes_to = list(merged.values()), unassigned_code_refs = unassigned, deployment = first.deployment,
        wall_ms = max((p.wall_ms or 0) for p in parts) or None,
    )


def route_unrouted_sources(
    per_source: list[schemas.SourceDigest],
    outline_sections: list[dict],
    bodies: dict[str, str],
) -> tuple[list[schemas.SourceDigest], list[str]]:
    """Give every source the digest routed to NO section a contribution to the outline section its own title and summary
    resemble most (words weighted by how few sections use them; earliest on a tie), with all of its code blocks. The planner placed the page in this chapter, so dropping it
    here silently loses its facts and code (asyncio ch-05: a weak digest model returned `contributes_to: []` for the "Developing
    with asyncio" page; its 22 code blocks never reached a section and the chapter printed 13 of 35). Returns (per_source,
    source_keys that were routed)."""
    if not outline_sections:
        return per_source, []
    sec_words = [(s.get("section_id"), _words(f"{s.get('heading', '')} {s.get('description', '')}")) for s in outline_sections]
    out: list[schemas.SourceDigest] = []
    routed: list[str] = []
    for src in per_source:
        if src.contributes_to:
            out.append(src)
            continue
        src_words = _words(f"{src.source_title} {src.overall_summary}")
        # a word in few sections tells them apart (blocking, executor), one in every section does not (event, loop)
        best = max(range(len(sec_words)), key = lambda i: (
            sum(1 / sum(w in ws for _, ws in sec_words) for w in src_words & sec_words[i][1]), -i,
        ))
        summary = " ".join(src.overall_summary.split())
        facts = [
            f.strip() for f in re.split(r"(?<=[.!?])\s+", summary)
            if params.KEY_FACT_MIN_CHARS <= len(f.strip()) <= params.KEY_FACT_MAX_CHARS
        ][:params.MAX_KEY_FACTS_PER_CONTRIB] or [summary[:params.KEY_FACT_MAX_CHARS]]
        try:
            contrib = schemas.SectionContribution(
                section_id = str(sec_words[best][0]), source_key = src.source_key, relevance = "supporting",
                summary = summary[:params.SUMMARY_MAX_CHARS], key_facts = facts,
                code_refs = extract_vault_hashes(bodies.get(src.source_key) or ""),
            )
        except ValidationError:
            out.append(src)
            continue
        out.append(src.model_copy(update = {"contributes_to": [contrib], "unassigned_code_refs": []}))
        routed.append(src.source_key)
    return out, routed


def drop_empty_sections(per_source: list[schemas.SourceDigest], outline_sections: list[dict], merged: dict[str, str]) -> dict[str, str]:
    """Outline sections no source was routed to, even after the pool merge, have nothing to be written from: the writer fell back to the whole chapter's sources
    and invented generic topics (fastmcp ch-01, outline of 12 for 18 client pages: 'Streaming Responses', 'Error Handling'... with 3 prose-only subtopics each). They are
    folded like merged sections (loser → the live section whose heading and description they resemble most, earliest on a tie) so the writer skips them. At least one
    section stays. Returns the merge map extended with them."""
    ids = [s.get("section_id") for s in outline_sections]
    used = {_resolve_merge(merged, c.section_id) for src in per_source for c in src.contributes_to}
    live = [sid for sid in ids if sid not in merged]
    empty = [sid for sid in live if sid not in used]
    kept = [sid for sid in live if sid in used]
    if not empty or not kept:
        return merged
    words = {s.get("section_id"): _words(f"{s.get('heading', '')} {s.get('description', '')}") for s in outline_sections}
    out = dict(merged)
    for sid in empty:
        out[sid] = max(kept, key = lambda k: (len(words.get(sid, set()) & words.get(k, set())), -ids.index(k)))
    return out


def _plain_excerpt(md_text: str, limit: int) -> str:
    """The page's prose with code-block sentinels, heading marks and markdown punctuation removed, cut at `limit` characters on a word boundary."""
    t = re.sub(r"<code-ref[^>]*>", " ", md_text or "")
    t = re.sub(r"^#{1,6}\s*", "", t, flags = re.M)
    t = re.sub(r"[`*_>|]+", " ", t)
    t = " ".join(t.split())
    if len(t) <= limit:
        return t
    return t[:limit].rsplit(" ", 1)[0]


def stub_digest_for_failed(source_key: str, md_text: str) -> Optional[schemas.SourceDigest]:
    """A source whose digest call failed every attempt keeps its place: its title and the opening of its text stand in for the LLM
    summary, with no contribution yet, so `route_unrouted_sources` gives it to the section it resembles most together with ALL of its
    code blocks (fastmcp ch-01: the one failed page, mcp-json-configuration, took ~30 blocks out of the chapter in every run). None
    when the page has no usable text."""
    summary = _plain_excerpt(md_text, params.OVERALL_SUMMARY_MAX_CHARS - 20)
    if len(summary) < params.OVERALL_SUMMARY_MIN_CHARS:
        return None
    try:
        return schemas.SourceDigest(
            source_key = source_key, source_title = derive_source_title_fallback(md_text, source_key),
            overall_summary = summary, contributes_to = [], unassigned_code_refs = extract_vault_hashes(md_text),
        )
    except ValidationError:
        return None


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


def is_context_overflow_error(e: Exception) -> bool:
    """Heuristic substring match — same idiom as outline_sdp's classifier.
    The Rotator is a universal gateway with no context-length-aware arm
    filtering, so even a single 100K-char source can exceed a small
    -context arm from a heterogeneous multi-provider pool."""
    msg = str(e).lower()
    return any(marker in msg for marker in params.CONTEXT_OVERFLOW_MARKERS)


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
    m = patterns.JSON_RE.search(text)
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


def trim_key_facts(raw: dict) -> dict:
    """Bring each contribution's key_facts inside the schema bounds instead of rejecting the whole source: duplicates collapse, a
    fact under KEY_FACT_MIN_CHARS is dropped, one over KEY_FACT_MAX_CHARS is cut at a word boundary, and a list over
    MAX_KEY_FACTS_PER_CONTRIB keeps its first entries (fastmcp ch-01: 'key_facts count must be 1-5; got 6' failed two repairs and the
    source was lost). A list that would end up empty is left for the validator to reject. Returns a copy; valid input is unchanged."""
    contribs = raw.get("contributes_to") if isinstance(raw, dict) else None
    if not isinstance(contribs, list):
        return raw
    out = []
    for c in contribs:
        facts = c.get("key_facts") if isinstance(c, dict) else None
        if isinstance(facts, list) and all(isinstance(f, str) for f in facts):
            kept: list[str] = []
            for f in facts:
                t = " ".join(f.split())
                if len(t) > params.KEY_FACT_MAX_CHARS:
                    t = t[:params.KEY_FACT_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") or t[:params.KEY_FACT_MAX_CHARS]
                if len(t) >= params.KEY_FACT_MIN_CHARS and t not in kept:
                    kept.append(t)
            if kept:
                c = {**c, "key_facts": kept[:params.MAX_KEY_FACTS_PER_CONTRIB]}
        out.append(c)
    return {**raw, "contributes_to": out}


def try_parse_payload(
    raw: dict,
) -> tuple[Optional[schemas.LLMDigestPayload], Optional[str]]:
    try:
        return schemas.LLMDigestPayload.model_validate(trim_key_facts(raw)), None
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
