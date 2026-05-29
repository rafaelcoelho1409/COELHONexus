"""render_audit_write — Materialize + audit + persist library.

Pure module: Pydantic schemas + Jinja2 inline templates + rendering
functions + SHA-256 round-trip audit + per-source vault merger.
No I/O, no LLM calls — that lives in `synth/render/node.py`.

ARCHITECTURE — pure deterministic transform, ZERO LLM calls

  Inputs:
    - sawc-latest.json    (ChapterDraft from sawc_write)
    - mgsr-latest.json    (halt decision; v1 confirms halt=true)
    - planner plan-latest (chapter.sources list)
    - synth-vault/{slug}/pages/{idx}-{safe_slug}.vault.json (per source)

  Outputs (4 MinIO artifacts per chapter):
    - synth/{slug}/{chapter_id}/README.md
    - synth/{slug}/{chapter_id}/challenges.md
    - synth/{slug}/{chapter_id}/flashcards.json
    - synth/{slug}/{chapter_id}/render-latest.json (RenderResult metadata)

THE ROUND-TRIP AUDIT (the integrity guarantee)

  Per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` §9:
  "Round-trip audit: re-hash every materialized code block, assert
   byte-identical to vault → on any drift, structured retry."

  Concretely:
    1. For each `section.subtopics[*].code_ref_hash` in the ChapterDraft
       (v2 cookbook schema, 2026-05-24):
       a. Look up the merged vault → get VaultEntry.fence_text
       b. Re-hash fence_text with SHA-256[:16] (same algorithm vault.py used)
       c. Assert rehashed_prefix == code_ref_hash → byte_drift list otherwise
       d. If not found in any source vault → add to `missing` list
    2. After rendering, scan the chapter markdown for ANY
       `<code-ref hash=".../>"` remnants → if > 0, sentinel substitution
       failed (a render bug). Count goes into `sentinels_in_output`.
    3. Compute `orphan_unused`: vault entries that were never referenced
       by any section. Informational, not an audit failure.

  audit_passed = (
      no missing
      AND no byte_drift
      AND sentinels_in_output == 0
  )

WHY BYTE-EXACT IS POSSIBLE HERE (vs the literature's harder problem)

  arXiv 2601.03640 (Jan 2026) — Verbatim Data Transcription Failures —
  shows LLMs SILENTLY DROP entries from long literal payloads. Their
  measurement: "many model outputs contain none of the expected values"
  on large lists.

  Our pipeline sidesteps this entirely by NEVER ASKING THE LLM TO COPY
  CODE. The vault sentinel architecture (vault.py, ingestion-time) hides
  fenced code blocks behind `<code-ref hash="..."/>` opaque tags before
  any LLM ever sees the document. The LLM picks WHICH hash goes in each
  subtopic (sawc Section.subtopics[*].code_ref_hash); render_audit_write
  does the actual text materialization deterministically. Stronger
  guarantee than RTC (arXiv 2402.08699 semantic equivalence) — byte-exact.

TEMPLATE DESIGN — Jinja2 inline strings, no external .j2 files

  Three templates (CHAPTER_MD_TEMPLATE, CHALLENGES_MD_TEMPLATE,
  artifacts metadata is just JSON dump). Inline strings keep the
  whole module self-contained — no template-file path issues in
  the k3d container, no need for Jinja2 file-system loaders.

  Section context is PRE-PROCESSED in Python (materialize code refs
  via vault lookup, derive source_basename from citation.source_key)
  so the Jinja templates stay readable + dumb. The renderer is
  IDEMPOTENT (same inputs → byte-identical output, every run).

TUNABLES

  RENDER_SCHEMA_VERSION  = "1.0"
  RENDER_TEMPLATE_VERSION = "v1-2026-05-19"
  _SENTINEL_RE  → matches `<code-ref hash=".../>"` for the post-render
                   audit scan (defense in depth)
"""
from __future__ import annotations

import hashlib
import json
import re

from .constants import (
    CHAPTER_MD_TEMPLATE,
    CHALLENGES_MD_TEMPLATE,
    _JINJA_ENV,
    _SENTINEL_RE,
    _VAULT_HASH_LEN,
)
from .types import AuditResult, CodeRefResolution


# =============================================================================
# Section context preprocessing
# =============================================================================
def _basename(key: str) -> str:
    """Extract the last `/`-segment of a MinIO key. Robust to trailing
    slashes; falls back to the full key if no slash."""
    if not key:
        return ""
    return key.rstrip("/").rsplit("/", 1)[-1]


def _slugify(s: str) -> str:
    """Markdown-anchor-friendly slug for TOC links."""
    import re as _re
    out = _re.sub(r"[^a-zA-Z0-9\s-]", "", (s or "")).strip().lower()
    return _re.sub(r"\s+", "-", out)


def build_section_context(
    section: dict,
    *,
    vault: dict[str, str],
    resolution_log: list[CodeRefResolution],
) -> dict:
    """Pre-process one sawc Section (v2 cookbook schema) into the Jinja
    template context.

    Side effect: APPENDS one `CodeRefResolution` entry per subtopic
    code_ref_hash to `resolution_log`. This lets the caller compute
    audit stats in a single pass.

    Per-subtopic logic (3-tier audit, Ship #96, 2026-05-24):
      - code_source == "verbatim" (or unset legacy): substitute from
        vault[hash]; verify byte-drift via re-hash → tier='verbatim',
        or 'hallucinated' on miss/drift.
      - code_source == "derived" with derived_code: wrap derived body
        in a fenced block; emit a Markdown caption above. AST-parse
        the body → tier='derived', or 'hallucinated' on AST failure.

    Returns a dict with:
      - section_id, heading, intro, anchor
      - subtopics: list[{subheading, explanation, code_block, anchor,
                          code_source, derived_caption}]
      - citations: list[{source_basename, claim}]
    """
    # Lazy import to avoid a render→sawc_derive cycle; service.python_ast_valid
    # is the canonical hallucination gate for derived bodies.
    from ..sawc_derive.service import python_ast_valid as _ast_valid

    section_id = section.get("section_id", "?")
    heading = section.get("heading", "?")
    intro = (section.get("intro") or "").strip()

    sub_ctx: list[dict] = []
    for sub in (section.get("subtopics") or []):
        if not isinstance(sub, dict):
            continue
        subheading = (sub.get("subheading") or "").strip()
        explanation = (sub.get("explanation") or "").strip()
        h = sub.get("code_ref_hash")
        code_source = sub.get("code_source") or "verbatim"
        derived_code = sub.get("derived_code") or ""

        code_block = ""
        derived_caption = ""

        if code_source == "derived" and derived_code:
            # ── Derived path ──────────────────────────────────────────
            ast_ok = _ast_valid(derived_code)
            # Wrap in a python fence — the audit scans for sentinels,
            # not for fences, so this is benign.
            body = derived_code.rstrip()
            code_block = f"```python\n{body}\n```"
            short_hash = (h or "")[:8] if h else ""
            derived_caption = (
                f"> _Derived example (AI-generated, expanded from doc "
                f"reference `{short_hash}…` via Analogical Prompting + "
                f"MPSC; AST-validated)._\n"
                if ast_ok else
                f"> _Derived example (AI-generated, expanded from doc "
                f"reference `{short_hash}…`; AST parse FAILED — flagged "
                f"as hallucinated in audit)._\n"
            )
            tier = "derived" if ast_ok else "hallucinated"
            resolution_log.append(CodeRefResolution(
                hash=h or "",
                found_in_vault=False,   # derived isn't FROM vault
                byte_drift=False,
                materialized_chars=len(body),
                section_id=section_id,
                tier=tier,
            ))
        elif h:
            # ── Verbatim path (default) ───────────────────────────────
            if h in vault:
                fence_text = vault[h]
                code_block = fence_text
                rehashed = _hash_block(fence_text)
                byte_drift = (rehashed != h)
                tier = "hallucinated" if byte_drift else "verbatim"
                resolution_log.append(CodeRefResolution(
                    hash=h,
                    found_in_vault=True,
                    byte_drift=byte_drift,
                    materialized_chars=len(fence_text),
                    section_id=section_id,
                    tier=tier,
                ))
            else:
                resolution_log.append(CodeRefResolution(
                    hash=h,
                    found_in_vault=False,
                    byte_drift=False,
                    materialized_chars=0,
                    section_id=section_id,
                    tier="hallucinated",
                ))
        sub_ctx.append({
            "subheading":       subheading,
            "explanation":      explanation,
            "code_block":       code_block,
            "anchor":           _slugify(subheading),
            "code_source":      code_source,
            "derived_caption":  derived_caption,
        })

    citations = []
    for c in (section.get("citations") or []):
        src = c.get("source_key") if isinstance(c, dict) else None
        claim = c.get("claim") if isinstance(c, dict) else None
        if src and claim:
            citations.append({
                "source_basename": _basename(src),
                "claim": claim,
            })

    return {
        "section_id":  section_id,
        "heading":     heading,
        "anchor":      _slugify(heading),
        "intro":       intro,
        "subtopics":   sub_ctx,
        "citations":   citations,
    }


# =============================================================================
# Write-path quality pass — within-chapter recycling + mismatch
# (docs/DD-SYNTH-SECTION-RECYCLING-2026-05-29.md, fixes #1 + #4)
# =============================================================================
# Runs AFTER build_section_context (so resolution_log / audit counts are
# already fixed) and BEFORE render_chapter_md. It ONLY rewrites the
# `code_block` string of a subtopic — never touches code_ref_hash, the
# resolution_log, or introduces sentinels — so the round-trip audit verdict
# is unaffected (a deduped/dropped block was still "resolved"; it just isn't
# displayed).
#
#   #1 cross-section body dedup — a non-trivial code BODY that already
#      appeared in an earlier subtopic (anywhere in the chapter) is replaced
#      with a one-line cross-reference to its first occurrence; the first
#      occurrence is kept. Catches the ~45% within-chapter recycling that
#      vault-HASH dedup misses: identical fence_text coming from different
#      source docs carries different (salted) hashes, so a hash-level dedup
#      never sees them as the same.
#   #4 per-subtopic mismatch — a non-trivial code body whose identifiers have
#      ZERO overlap with its subheading+explanation is replaced with a short
#      "omitted — misrouted" note (prose kept). Catches the "theme JSON under
#      an Install heading" class. Deliberately conservative (needs ≥N code
#      identifiers + a clean zero overlap) and env-toggleable so a false
#      positive is visible and easy to disable.

# Dedup only bodies with real heft — tiny one-liners (e.g. `claude --version`)
# recur legitimately and must NOT be cross-referenced away.
_DEDUP_MIN_LINES = 3
_DEDUP_MIN_CHARS = 80
# Mismatch needs enough identifiers to judge + a clean zero overlap.
_MISMATCH_MIN_CODE_IDENTS = 4

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_NOISE_IDENTS = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "import", "async",
    "await", "def", "class", "return", "none", "true", "false", "str", "int",
    "self", "get", "set", "use", "run", "via", "your", "null", "var", "let",
    "const", "new", "function", "type", "name", "value", "data", "code",
})


def _code_inner(code_block: str) -> str:
    """Strip the leading ```lang line and trailing ``` fence from a
    materialized code_block, returning the inner body. '' if not fenced."""
    if not code_block or "```" not in code_block:
        return ""
    body = code_block.strip()
    nl = body.find("\n")
    if nl == -1:
        return ""
    body = body[nl + 1:]            # drop the opening ```lang line
    end = body.rfind("```")
    if end != -1:
        body = body[:end]           # drop the closing fence
    return body.strip("\n")


def _norm_body(inner: str) -> str:
    """Whitespace-collapsed body for duplicate detection. Conservative —
    no comment stripping (avoids false merges); indentation-insensitive."""
    return re.sub(r"\s+", " ", inner).strip()


def _idents(text: str) -> set[str]:
    return {w.lower() for w in _IDENT_RE.findall(text or "")} - _NOISE_IDENTS


def dedupe_and_align_sections(
    sections_ctx: list[dict],
    *,
    drop_mismatch: bool = True,
) -> dict:
    """Mutate `sections_ctx` in place; return {n_dedup, n_mismatch}.

    See the module note above. Pure + deterministic given identical input."""
    seen: dict[str, tuple[str, str, str]] = {}   # norm_body -> (heading, sub, anchor)
    n_dedup = 0
    n_mismatch = 0
    for sec in sections_ctx:
        heading = sec.get("heading") or "?"
        for sub in (sec.get("subtopics") or []):
            inner = _code_inner(sub.get("code_block") or "")
            if not inner:
                continue
            subheading = sub.get("subheading") or "?"
            nontrivial = (
                inner.count("\n") + 1 >= _DEDUP_MIN_LINES
                or len(inner) >= _DEDUP_MIN_CHARS
            )

            # #1 — cross-section body dedup (non-trivial bodies only)
            if nontrivial:
                key = _norm_body(inner)
                prev = seen.get(key)
                if prev and (prev[0], prev[1]) != (heading, subheading):
                    fh, fsub, fanchor = prev
                    ref = (f"[**{fh} → {fsub}**](#{fanchor})"
                           if fanchor else f"**{fh} → {fsub}**")
                    sub["code_block"] = (
                        f"> _↳ Same code as {ref}; shown once, not repeated._"
                    )
                    sub["derived_caption"] = ""
                    n_dedup += 1
                    continue

            # #4 — per-subtopic mismatch (kept blocks only)
            if drop_mismatch:
                ci = _idents(inner)
                if len(ci) >= _MISMATCH_MIN_CODE_IDENTS:
                    ti = _idents(subheading + " " + (sub.get("explanation") or ""))
                    if not (ci & ti):
                        sub["code_block"] = (
                            "> _(Code example omitted — it did not match this "
                            "subtopic and was likely misrouted.)_"
                        )
                        sub["derived_caption"] = ""
                        n_mismatch += 1
                        continue   # do NOT register a misrouted block as canonical

            if nontrivial:
                seen.setdefault(
                    key, (heading, subheading, sub.get("anchor") or ""),
                )
    return {"n_dedup": n_dedup, "n_mismatch": n_mismatch}


# =============================================================================
# Rendering — three pure transforms
# =============================================================================
def _build_toc(sections_ctx: list[dict]) -> list[dict]:
    """Build a nested TOC from sections_ctx for the cookbook chapter
    template. Only emitted when there are ≥2 sections with subtopics."""
    toc = []
    for s in sections_ctx:
        subs = s.get("subtopics") or []
        toc.append({
            "heading": s.get("heading") or "?",
            "anchor":  s.get("anchor") or "",
            "subtopics": [
                {"subheading": x.get("subheading") or "?",
                 "anchor":     x.get("anchor") or ""}
                for x in subs if (x.get("subheading") or "").strip()
            ],
        })
    return toc


def render_chapter_md(
    chapter_title: str,
    sections_ctx: list[dict],
) -> str:
    """Render the README.md (full cookbook chapter markdown). Deterministic
    given identical inputs.

    v2 cookbook structure: H1 chapter title → optional TOC → for each
    section: H2 heading + intro paragraph + for each subtopic: H3 +
    explanation + code block + (citations footer per section)."""
    toc = _build_toc(sections_ctx) if len(sections_ctx) >= 2 else []
    tpl = _JINJA_ENV.from_string(CHAPTER_MD_TEMPLATE)
    md = tpl.render(
        chapter_title=chapter_title,
        sections=sections_ctx,
        toc=toc,
    )
    # Collapse 3+ consecutive blank lines (Jinja's whitespace control
    # is good but not perfect with optional blocks). Two blank lines max.
    md = re.sub(r"\n{4,}", "\n\n\n", md)
    return md.rstrip() + "\n"


def render_challenges_md(
    chapter_title: str,
    challenges: list[str],
) -> str:
    """Render challenges.md — H1 title + numbered list."""
    tpl = _JINJA_ENV.from_string(CHALLENGES_MD_TEMPLATE)
    md = tpl.render(
        chapter_title=chapter_title,
        challenges=challenges or [],
    )
    return md.rstrip() + "\n"


def render_flashcards_json(flashcards: list[dict]) -> str:
    """Render flashcards.json — a JSON array of {q, a} objects.

    Sorting is NOT applied — we preserve outline_sdp's order so the
    flashcards align with the chapter's narrative arc."""
    # Normalize to {q, a} shape (drop other fields if present, keep
    # only the two we render)
    normalized = []
    for fc in flashcards or []:
        if not isinstance(fc, dict):
            continue
        q = (fc.get("q") or "").strip()
        a = (fc.get("a") or "").strip()
        if q and a:
            normalized.append({"q": q, "a": a})
    return json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"


# =============================================================================
# Vault loading + merging
# =============================================================================
def merge_vault_entries(per_source_manifests: list[dict]) -> dict[str, str]:
    """Merge a list of VaultManifest dicts into a single
    `{hash: fence_text}` lookup map.

    On a collision (same hash key in multiple sources — which shouldn't
    happen if vault.py salted correctly), the LAST source wins. This
    is a defensive fallback; emitting a warning is the caller's job.
    """
    merged: dict[str, str] = {}
    for m in per_source_manifests:
        entries = (m or {}).get("entries") or {}
        if not isinstance(entries, dict):
            continue
        for h, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            fence_text = entry.get("fence_text")
            if fence_text:
                merged[h] = fence_text
    return merged


def source_key_to_vault_key(source_key: str, framework_slug: str) -> str:
    """Translate `ingestion/{slug}/pages/{idx:04d}-{safe_slug}.md` to
    `synth-vault/{slug}/pages/{idx:04d}-{safe_slug}.vault.json`.

    This mirrors `ingestion/storage_minio.py:vault_manifest_key()`'s
    transformation without importing the helper (keeps this module
    independent of the ingestion subsystem)."""
    basename = _basename(source_key)
    # Strip the `.md` suffix; substitute with `.vault.json`
    if basename.endswith(".md"):
        basename = basename[:-3]
    return f"synth-vault/{framework_slug}/pages/{basename}.vault.json"


# =============================================================================
# Audit computation
# =============================================================================
def _hash_block(payload: str, salt: int = 0) -> str:
    """16-hex SHA-256 prefix. MUST match `synth/vault.py:_hash_block`
    or the audit will spuriously fail. Salt parameter kept for symmetry
    but unused at audit time (salt collisions are vault-time only)."""
    seed = payload if salt == 0 else f"{payload}|{salt}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_VAULT_HASH_LEN]


def compute_audit(
    *,
    resolution_log: list[CodeRefResolution],
    vault: dict[str, str],
    rendered_chapter_md: str,
) -> AuditResult:
    """Aggregate per-ref resolution log into an AuditResult.

    Counts:
      - n_code_refs_referenced: how many `code_refs` entries were
        in any section (= len(resolution_log))
      - n_resolved: how many were found in some source vault
      - n_missing: hashes referenced but not in vault (LLM hallucinated
        OR vault file was missing OR cross-source dedup conflict)
      - n_byte_drift: hashes whose materialized text doesn't re-hash
        back to the original (NEVER expected — vault.py is deterministic
        — but defense-in-depth)
      - n_orphan_unused: vault hashes that no section claimed
      - sentinels_in_output: count of `<code-ref hash=.../>` literals
        STILL present in the rendered chapter markdown — must be 0 for
        a passing audit
    """
    # Referenced hashes = ANY tier with a hash. Derived subtopics still
    # cite the originating thin vault entry via code_ref_hash for
    # provenance, so they count toward "referenced" — preventing the
    # underlying entry from being misclassified as orphan_unused.
    referenced_hashes: set[str] = {
        r.hash for r in resolution_log if r.hash
    }
    n_total = len(resolution_log)
    n_resolved = sum(1 for r in resolution_log if r.found_in_vault)
    n_missing = sorted({
        r.hash for r in resolution_log
        if r.tier == "hallucinated" and not r.found_in_vault and r.hash
    })
    n_drift = sorted({
        r.hash for r in resolution_log
        if r.byte_drift and r.hash
    })
    n_orphan = sorted(set(vault.keys()) - referenced_hashes)
    sentinels_left = len(_SENTINEL_RE.findall(rendered_chapter_md or ""))

    # Ship #96 — 3-tier counts.
    n_verbatim = sum(1 for r in resolution_log if r.tier == "verbatim")
    n_derived = sum(1 for r in resolution_log if r.tier == "derived")
    n_hallucinated = sum(1 for r in resolution_log if r.tier == "hallucinated")

    audit_passed = (
        not n_missing
        and not n_drift
        and sentinels_left == 0
        and n_hallucinated == 0
    )

    # Cap resolution_details to first 100 entries to keep the persisted
    # blob small. Audit verdict is independent of detail count.
    capped_details = resolution_log[:100]

    return AuditResult(
        n_code_refs_referenced=n_total,
        n_resolved=n_resolved,
        n_missing=n_missing,
        n_orphan_unused=n_orphan,
        n_byte_drift=n_drift,
        sentinels_in_output=sentinels_left,
        audit_passed=audit_passed,
        n_verbatim=n_verbatim,
        n_derived=n_derived,
        n_hallucinated=n_hallucinated,
        resolution_details=capped_details,
    )


# =============================================================================
# Artifact hashing
# =============================================================================
def sha256_bytes(content: str) -> str:
    """Full 64-char SHA-256 of the content's UTF-8 bytes. Used for
    artifact provenance (NOT for the 16-hex vault-hash audit — different
    semantic)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
