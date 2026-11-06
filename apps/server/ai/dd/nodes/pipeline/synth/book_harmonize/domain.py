"""book_harmonize — pure helpers (manifest hash, sibling-claim selection, canonical-term formatter, violation validation, paragraph edits)."""
from __future__ import annotations
from . import params, patterns, versions

import hashlib
import re
from collections import Counter
from typing import Optional




def code_blocks_preserved(original: str, patched: str) -> bool:
    """True when every fenced code block in `original` appears byte-identical in `patched` (as a multiset). Chapter code is materialized byte-exact from the vault; a prose patch that reflows or rewrites it silently breaks that guarantee, so such a patch must be rejected rather than shipped."""
    before = Counter(m.group(0) for m in patterns.FENCED_BLOCK_RE.finditer(original or ""))
    after = Counter(m.group(0) for m in patterns.FENCED_BLOCK_RE.finditer(patched or ""))
    return not (before - after)


def _norm(text: object) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", str(text or "").lower()))


def validate_violations(
    violations: list, *, this_id: str, this_prose: str, claims_by_id: dict[str, list[str]], canonical_terms: list[dict],
) -> tuple[list[dict], list[str]]:
    """Keep only violations that are grounded: they quote this chapter verbatim and either name a sibling chapter whose extracted
    claims contain the conflicting claim, or (definition drift / terminology) name a canonical term. Returns (kept, why dropped)."""
    prose = _norm(this_prose)
    terms = [t for t in (_norm(x.get("term")) for x in canonical_terms or []) if t]
    kept: list[dict] = []
    dropped: list[str] = []
    for v in violations or []:
        if not isinstance(v, dict):
            continue
        kind = str(v.get("kind") or "issue").lower()
        quote = _norm(v.get("this_chapter_says"))
        if len(quote.split()) < params.MIN_QUOTE_WORDS or quote not in prose:
            dropped.append(f"{kind}: the quoted words are not in the chapter")
            continue
        sib = str(v.get("sibling_chapter") or "").strip()
        sib_id = next((c for c in claims_by_id if c != this_id and len(sib) >= 3 and (c == sib or c.startswith(sib) or sib in c)), None)
        claim = set(_norm(v.get("sibling_claim")).split())
        grounded = False
        if sib_id and len(claim) >= 3:
            known = set(_norm(" ".join(claims_by_id.get(sib_id) or [])).split())
            grounded = len(claim & known) >= params.CLAIM_MATCH_SHARE * len(claim)
        if not grounded and kind in ("definition_drift", "terminology_divergence"):
            blob = f" {_norm(str(v.get('sibling_claim') or '') + ' ' + str(v.get('should_say') or ''))} "
            grounded = any(f" {t} " in blob for t in terms)
        if not grounded:
            dropped.append(f"{kind}: no sibling claim or canonical term backs it")
            continue
        kept.append(v)
    return kept, dropped


def editable_paragraph(par: str) -> bool:
    """A prose paragraph (not a heading, list, table, fence or comment)."""
    p = par.strip()
    return bool(p) and not p.startswith(("```", "~~~", "#", "- ", "* ", "|", ">", "<!--", "!["))


def paragraph_for_quote(paragraphs: list[str], quote: str) -> Optional[int]:
    """Index of the first editable paragraph that contains the quoted words."""
    q = _norm(quote)
    for i, par in enumerate(paragraphs):
        if editable_paragraph(par) and q and q in _norm(par):
            return i
    return None


def paragraph_patch_ok(original: str, edited: Optional[str]) -> bool:
    """The edited paragraph is non-empty prose of about the original's length with no code fence."""
    if not edited or not edited.strip() or "```" in edited or "~~~" in edited:
        return False
    ratio = len(edited.strip()) / max(1, len(original.strip()))
    return params.PATCH_PARAGRAPH_MIN_RATIO <= ratio <= params.PATCH_PARAGRAPH_MAX_RATIO


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
        f"prompt={versions.BOOK_HARMONIZE_PROMPT_VERSION}|"
        f"schema={versions.BOOK_HARMONIZE_SCHEMA_VERSION}"
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

