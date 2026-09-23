"""chapter_propose — pure helpers (target sizing, structural seed
extraction, summary helper, JSON parse, manifest hash). Prompt builders
live in prompts.py; Pydantic schemas in schemas.py."""
from __future__ import annotations
from . import params, patterns, schemas, versions

import json
from collections import Counter
from hashlib import sha256
from typing import Optional

import json_repair  # type: ignore



def target_chapters_for_n_docs(n_docs: int) -> int:
    """Per-corpus target chapter count (guides the proposer + optimal-
    stopping floor). Clamped to [PROPOSALS_TARGET_FLOOR,
    PROPOSALS_TARGET_CEILING]."""
    if n_docs <= 0:
        return params.PROPOSALS_TARGET_FLOOR
    return min(
        params.PROPOSALS_TARGET_CEILING,
        max(params.PROPOSALS_TARGET_FLOOR, round(n_docs / params.PROPOSALS_DIVISOR)),
    )


def _extract_h12_headings(body: str, max_n: int) -> list[str]:
    """First N H1/H2 headings from a markdown body. Fenced code blocks are
    stripped first — see FENCED_CODE_RE."""
    out: list[str] = []
    body_no_code = patterns.FENCED_CODE_RE.sub("", body or "")
    for m in patterns.H2_RE.finditer(body_no_code):
        h = " ".join(m.group(1).strip().split())
        h = patterns.LEADING_MD_LINK_RE.sub("", h)
        h = patterns.TRAILING_MD_LINK_RE.sub("", h).strip()
        if not h or h.casefold() in params.GENERIC_HEADINGS:
            continue
        out.append(h)
        if len(out) >= max_n:
            break
    return out


def _namespace_from_key(source_key: str) -> Optional[str]:
    """Extract a 'namespace' from a source key. Captures CLI subcommand
    patterns + file-tree top-level directories under `commands/`."""
    m = patterns.CLI_PATTERN_RE.search(source_key)
    if m:
        return m.group(1).lower()
    parts = source_key.split("/")
    if len(parts) >= 4 and parts[0] == "ingestion":
        # e.g. ingestion/claude-code/pages/0012-foo.md → "pages"
        return parts[2].lower()
    return None


def extract_structural_seeds(
    *,
    source_keys: list[str],
    bodies_by_key: dict[str, str],
) -> dict:
    """Top-level headings + CLI namespace seeds from the corpus for chapter_propose."""
    headings_counter: Counter[str] = Counter()
    for key in source_keys:
        body = bodies_by_key.get(key) or ""
        for h in _extract_h12_headings(body, max_n = 4):
            headings_counter[h] += 1

    namespaces_counter: Counter[str] = Counter()
    for key in source_keys:
        ns = _namespace_from_key(key)
        if ns:
            namespaces_counter[ns] += 1

    # chapter seed. Keep ones that occur ≥ 2.
    seed_headings = [
        h for h, n in headings_counter.most_common(params.SEED_MAX_HEADINGS)
        if n >= 2
    ][:params.SEED_MAX_HEADINGS]
    seed_namespaces = [
        ns for ns, n in namespaces_counter.most_common(params.SEED_MAX_NAMESPACES)
        if n >= 2
    ]

    return {
        "headings":   seed_headings,
        "namespaces": seed_namespaces,
    }


def build_fallback_proposals(
    framework: str, seeds: dict, target_chapters: int, n_docs: int,
) -> schemas.ChapterProposalList:
    """Deterministic fallback when all LLM samples fail (timeout/402). Uses
    structural seeds (headings/namespaces) so pipeline never returns 0 chapters
    and downstream never silently succeeds with empty plan. Mirrors doc_distill
    fallback distillate pattern."""
    headings = (seeds.get("headings") or [])[:]
    namespaces = (seeds.get("namespaces") or [])[:]
    # Target clamped to schema range
    n = max(params.PROPOSALS_MIN, min(params.PROPOSALS_MAX, target_chapters))
    generic = ["Core Concepts", "Configuration", "API Reference", "Guides", "Advanced Topics", "Troubleshooting", "Examples", "Best Practices"]

    # Dedup on the FINAL title (post word-count normalization below), case-
    # insensitively. Two reasons this has to happen in one place: (1)
    # headings_counter (domain.py) never case-normalizes, so two docs'
    # headings differing only in case (e.g. "How It Works" vs "How it
    # Works") both survive as distinct seed strings; (2) the old code
    # dedup-checked the RAW title but only applied the 2-8-word
    # normalization afterward in a separate loop, so two distinct raw
    # titles could still collide once truncated/suffixed. Either way a
    # case-sensitive "not in candidates" check here let both through, only
    # to collide on ChapterProposalList's case-insensitive uniqueness
    # validator — crashing the fallback itself with no further recovery
    # path. Confirmed live 2026-09-09 on the fastmcp corpus (duplicate
    # 'How It Works' after the LLM path had already failed all samples).
    candidates: list[str] = []
    seen: set[str] = set()

    def _try_add(raw_title: str) -> bool:
        words = raw_title.split()
        title = raw_title
        if len(words) < 2:
            title = title + " Overview"
        elif len(words) > 8:
            title = " ".join(words[:8])
        key = title.casefold()
        if key in seen:
            return False
        seen.add(key)
        candidates.append(title)
        return True

    # Prefer headings (human-written) then namespaces (file-tree) then generic.
    for h in headings:
        if len(candidates) >= n:
            break
        _try_add(h)
    for ns in namespaces:
        if len(candidates) >= n:
            break
        _try_add(ns.replace("-", " ").title())
    for g in generic:
        if len(candidates) >= n:
            break
        _try_add(g)

    proposals = [
        schemas.ChapterProposal(
            title = title,
            description = f"Covers {title.lower()} in {framework} based on structural signals from {n_docs} docs.",
            key_concepts = [title.lower().replace(" ", "_") + "_1", title.lower().replace(" ", "_") + "_2", title.lower().replace(" ", "_") + "_3"],
        )
        for title in candidates
    ]
    return schemas.ChapterProposalList(proposals = proposals)


def summarize_proposal(props: list[schemas.ChapterProposal]) -> dict:
    """Compact summary for the USC vote picker."""
    return {
        "n_chapters":         len(props),
        "titles":             [p.title for p in props],
        "max_concept_count":  max(
            (len(p.key_concepts) for p in props), default = 0,
        ),
        "total_concepts":     sum(len(p.key_concepts) for p in props),
    }


def parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = patterns.JSON_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json_repair.loads(m.group(0))  # type: ignore
        except Exception:
            return None


def try_validate(
    d: dict,
) -> tuple[Optional[schemas.ChapterProposalList], Optional[str]]:
    try:
        return schemas.ChapterProposalList.model_validate(d), None
    except Exception as e:
        return None, str(e)[:300]


def manifest_hash(
    *,
    slug: str,
    source_keys: list[str],
    distill_ref: Optional[str],
) -> str:
    h = sha256()
    h.update(versions.PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(source_keys):
        h.update(b"|")
        h.update(k.encode())
    h.update(b"|distill=")
    h.update((distill_ref or "").encode())
    return h.hexdigest()[:16]
