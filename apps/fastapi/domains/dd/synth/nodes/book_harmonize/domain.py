"""book_harmonize — pure helpers (manifest hash, sibling-claim selection,
canonical-term formatter, LLM-response JSON extraction)."""
from __future__ import annotations
from . import patterns, versions

import hashlib
import json
from typing import Optional

import json_repair  # type: ignore



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


def extract_first_json_object(text: str, start_from: int = 0) -> Optional[str]:
    """Return the first *balanced* {...} substring in text at or after
    `start_from`, tracking brace depth and skipping over quoted-string
    content (so a brace inside a string literal doesn't affect depth).
    Issue #15 follow-up, 2026-09-06: the original
    `_JSON_RE = re.compile(r"\\{.*\\}", DOTALL)` is greedy across the
    *entire* response — confirmed live on ch-10 (4,692-char response,
    real content, still failed both json.loads AND json_repair) — if
    claim/term text itself quotes a code snippet or JSON example
    containing stray braces, the greedy match spans from the first real
    '{' to a much later, unrelated '}' and hands both parsers an
    unrecoverable hybrid. This never over-extends: it stops at the first
    point brace depth returns to 0. Returns None if the brace opened at
    `start_from` never finds its match (e.g. truncated mid-structure, or
    — issue #16 — a stray duplicate '{' with no closing partner of its
    own; see `all_balanced_json_candidates` for the retry that handles
    that case)."""
    start = text.find("{", start_from)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None  # never closed — e.g. truncated mid-structure


# Issue #16, 2026-09-06/07: confirmed live on ch-10 — response started
# `{\n{"claims": [...` (a bare, never-closed '{' immediately followed by
# a newline and the REAL object's opening '{'). Starting the balanced
# scan only from the first '{' in the text can never recover this — that
# outer brace has no matching close anywhere in the response. Bounded to
# a handful of attempts so a pathological response can't blow up cost.
_MAX_BRACE_START_ATTEMPTS = 5


def all_balanced_json_candidates(text: str) -> list[str]:
    """Every balanced {...} substring found by retrying the scan from
    each successive '{' in text, up to `_MAX_BRACE_START_ATTEMPTS`
    distinct starting positions. Handles a duplicated/stray leading
    brace (issue #16): the first '{' fails to close, but the very next
    one opens a perfectly valid, parseable object."""
    candidates: list[str] = []
    search_from = 0
    for _ in range(_MAX_BRACE_START_ATTEMPTS):
        start = text.find("{", search_from)
        if start == -1:
            break
        block = extract_first_json_object(text, start)
        if block is not None:
            candidates.append(block)
        # Always advance past THIS '{', whether it closed or not, so a
        # duplicated-leading-brace case still reaches the real object
        # that opens immediately after the failed one.
        search_from = start + 1
    return candidates


def parse_json_block(raw: Optional[str]) -> Optional[dict]:
    """Extract + parse a JSON object out of a raw LLM response. Tries
    every balanced brace-matched candidate first (strict json.loads,
    then json_repair on each — issue #16's duplicated-leading-brace
    fix), then falls back to the original greedy whole-response regex
    (issue #15, 2026-09-06: ch-06's response_format={"type":"json_object"}
    call still returned non-strict JSON — single quotes/trailing commas/
    preamble — and the bare json.loads here had zero recovery path,
    unlike every other structured-output call site in this codebase).
    json_repair is the same idiom already used in
    domains/dd/planner/nodes/*/domain.py and domains/ycs/rag. Returns
    None (never raises) if nothing can be recovered at all — callers are
    responsible for logging that outcome, since a silent None here is
    exactly what made issue #15 hard to diagnose."""
    text = raw or ""
    candidates: list[str] = all_balanced_json_candidates(text)
    m = patterns.JSON_RE.search(text)
    if m and m.group(0) not in candidates:
        candidates.append(m.group(0))  # last-resort: pre-#15 behavior

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    for candidate in candidates:
        try:
            return json_repair.loads(candidate)  # type: ignore
        except Exception:
            continue
    return None
