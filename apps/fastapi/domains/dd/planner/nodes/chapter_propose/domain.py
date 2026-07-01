"""chapter_propose — pure helpers (target sizing, structural seed
extraction, summary helper, JSON parse, manifest hash). Prompt builders
live in prompts.py; Pydantic schemas in schemas.py."""
from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from typing import Optional

from .params import (
    GENERIC_HEADINGS,
    PROPOSALS_DIVISOR,
    PROPOSALS_TARGET_CEILING,
    PROPOSALS_TARGET_FLOOR,
    SEED_MAX_HEADINGS,
    SEED_MAX_NAMESPACES,
)
from .patterns import CLI_PATTERN_RE, H2_RE, JSON_RE
from .schemas import ChapterProposal, ChapterProposalList
from .versions import PROMPT_VERSION


def target_chapters_for_n_docs(n_docs: int) -> int:
    """Per-corpus target chapter count (guides the proposer + optimal-
    stopping floor). Clamped to [PROPOSALS_TARGET_FLOOR,
    PROPOSALS_TARGET_CEILING]."""
    if n_docs <= 0:
        return PROPOSALS_TARGET_FLOOR
    return min(
        PROPOSALS_TARGET_CEILING,
        max(PROPOSALS_TARGET_FLOOR, round(n_docs / PROPOSALS_DIVISOR)),
    )


def _extract_h12_headings(body: str, max_n: int) -> list[str]:
    """First N H1/H2 headings from a markdown body."""
    out: list[str] = []
    for m in H2_RE.finditer(body or ""):
        h = " ".join(m.group(1).strip().split())
        if h.casefold() in GENERIC_HEADINGS:
            continue
        out.append(h)
        if len(out) >= max_n:
            break
    return out


def _namespace_from_key(source_key: str) -> Optional[str]:
    """Extract a 'namespace' from a source key. Captures CLI subcommand
    patterns + file-tree top-level directories under `commands/`."""
    m = CLI_PATTERN_RE.search(source_key)
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
        h for h, n in headings_counter.most_common(SEED_MAX_HEADINGS)
        if n >= 2
    ][:SEED_MAX_HEADINGS]
    seed_namespaces = [
        ns for ns, n in namespaces_counter.most_common(SEED_MAX_NAMESPACES)
        if n >= 2
    ]

    return {
        "headings":   seed_headings,
        "namespaces": seed_namespaces,
    }


def summarize_proposal(props: list[ChapterProposal]) -> dict:
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
    m = JSON_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def try_validate(
    d: dict,
) -> tuple[Optional[ChapterProposalList], Optional[str]]:
    try:
        return ChapterProposalList.model_validate(d), None
    except Exception as e:
        return None, str(e)[:300]


def manifest_hash(
    *,
    slug: str,
    source_keys: list[str],
    distill_ref: Optional[str],
) -> str:
    h = sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(source_keys):
        h.update(b"|")
        h.update(k.encode())
    h.update(b"|distill=")
    h.update((distill_ref or "").encode())
    return h.hexdigest()[:16]
