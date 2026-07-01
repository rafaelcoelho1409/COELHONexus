"""book_harmonize — pure helpers (manifest hash, sibling-claim selection,
canonical-term formatter)."""
from __future__ import annotations

import hashlib

from .versions import (
    BOOK_HARMONIZE_PROMPT_VERSION,
    BOOK_HARMONIZE_SCHEMA_VERSION,
)


def compute_harmonize_manifest_hash(chapters: list[dict]) -> str:
    """Content-addressed cache key for the harmonize pass; keyed on prose + prompt version."""
    parts: list[str] = []
    for ch in sorted(chapters, key = lambda c: c.get("chapter_id", "")):
        cid = ch.get("chapter_id", "")
        prose = ch.get("prose") or ""
        prose_hash = hashlib.sha256(prose.encode("utf-8")).hexdigest()[:16]
        parts.append(f"{cid}={prose_hash}")
    payload = (
        f"chapters={'|'.join(parts)}|"
        f"prompt={BOOK_HARMONIZE_PROMPT_VERSION}|"
        f"schema={BOOK_HARMONIZE_SCHEMA_VERSION}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def pick_sibling_claims(
    this_id: str, claims_by_id: dict[str, list[str]],
) -> str:
    """Sample sibling-chapter claims into a context-safe blob. Cap at 40
    sibling claims total to keep the detect-prompt within budget."""
    sibling = []
    for cid, cs in claims_by_id.items():
        if cid == this_id:
            continue
        for c in cs[:6]:   # cap per chapter
            sibling.append(f"  [{cid}] {c}")
        if len(sibling) >= 40:
            break
    return "\n".join(sibling[:40])


def format_canonical_terms(canonical: list[dict]) -> str:
    if not canonical:
        return "(no terminology conflicts detected)"
    lines = []
    for t in canonical[:25]:
        name = (t.get("term") or "").strip()
        defn = (t.get("canonical_definition") or "").strip()
        if name:
            lines.append(f"  - {name}: {defn[:240]}")
    return "\n".join(lines)
