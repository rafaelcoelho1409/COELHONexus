"""render_audit_write — Materialize + audit + persist library.

Pure module: Pydantic schemas + Jinja2 inline templates + rendering
functions + SHA-256 round-trip audit + per-source vault merger.
No I/O, no LLM calls — that lives in `synth/nodes/render_audit_write.py`.

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
    1. For each `section.code_refs[*].hash` in the ChapterDraft:
       a. Look up the merged vault → get VaultEntry.fence_text
       b. Re-hash fence_text with SHA-256[:16] (same algorithm vault.py used)
       c. Assert rehashed_prefix == ref.hash  → byte_drift list otherwise
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
  any LLM ever sees the document. The LLM picks WHICH refs go in each
  section (sawc Section.code_refs); render_audit_write does the actual
  text materialization deterministically. Stronger guarantee than
  RTC (arXiv 2402.08699 semantic equivalence) — we get byte-exact.

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
from typing import Literal, Optional

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, Field


# =============================================================================
# Versioning
# =============================================================================
RENDER_SCHEMA_VERSION = "1.0"
RENDER_TEMPLATE_VERSION = "v1-2026-05-19"

# Same algorithm as `synth/vault.py:_hash_block` — 16-hex SHA-256
# prefix. MUST match or the audit will false-fail. If vault.py ever
# changes the prefix length, bump _VAULT_HASH_LEN here too.
_VAULT_HASH_LEN = 16
_HASH_ALGO = "sha256"

# Sentinel pattern from `synth/vault.py:_make_sentinel`. Used to scan
# the rendered output for ANY unresolved sentinels (would indicate a
# materialization bug). Lang attribute is optional (vault.py emits it
# only when lang is non-empty).
_SENTINEL_RE = re.compile(
    r'<code-ref hash="([0-9a-f]{16})"(?:\s+lang="[^"]*")?\s*/>'
)


ArtifactName = Literal["README.md", "challenges.md", "flashcards.json"]


# =============================================================================
# Pydantic schemas — persisted side
# =============================================================================
class RenderedArtifact(BaseModel):
    """One persisted MinIO blob produced by this node."""
    name:       ArtifactName
    minio_key:  str
    size_bytes: int
    sha256:     str    # full 64-char SHA-256 of the bytes (NOT the 16-hex
                       # vault-hash prefix). For audit/provenance.


class CodeRefResolution(BaseModel):
    """Per-code-ref audit detail. Useful for debugging vault drift."""
    hash:                str
    found_in_vault:      bool
    source_key:          Optional[str] = None
    byte_drift:          bool = False
    materialized_chars:  int = 0
    section_id:          str = ""        # which section referenced it


class AuditResult(BaseModel):
    """Round-trip audit summary. The `audit_passed` flag is the
    chapter-level go/no-go signal."""
    n_code_refs_referenced: int          # union across all sections
    n_resolved:             int          # found in some source vault
    n_missing:              list[str]    # referenced but not in any vault
    n_orphan_unused:        list[str]    # in vault but no section referenced
    n_byte_drift:           list[str]    # re-hash != vault hash
    sentinels_in_output:    int          # MUST be 0 — defense in depth
    audit_passed:           bool

    # Per-ref details for downstream debugging. Cap to keep blob small.
    resolution_details:     list[CodeRefResolution] = Field(default_factory=list)


class RenderResult(BaseModel):
    """Full render result — what gets persisted as render-latest.json.

    The three CONTENT artifacts (README.md / challenges.md /
    flashcards.json) live alongside this metadata blob in the same
    chapter prefix."""
    schema_version:        str = RENDER_SCHEMA_VERSION
    template_version:      str = RENDER_TEMPLATE_VERSION
    chapter_id:            str
    chapter_title:         str
    framework_slug:        str
    artifacts:             list[RenderedArtifact]   # 3 entries
    audit:                 AuditResult
    rendered_chars:        int                       # README.md size
    n_sections:            int
    n_paragraphs_total:    int
    n_citations_total:     int
    sawc_manifest_hash:    str
    mgsr_manifest_hash:    str
    render_manifest_hash:  str
    wall_ms:               int
    # The synth thread that produced this render. Persisted so the Study
    # chapter strip can re-open the chapter's LangGraph canvas (node
    # statuses) after a page refresh — the per-run thread_id is otherwise
    # ephemeral. Optional/defaulted so pre-existing blobs still parse.
    thread_id:             str = ""


# =============================================================================
# Jinja2 templates (inline)
# =============================================================================
# Render env — autoescape OFF (we produce markdown, not HTML). Strict
# undefined so an unset template var crashes loudly instead of producing
# silent `None` literals in the output.
_JINJA_ENV = Environment(
    autoescape=False,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    lstrip_blocks=True,
    trim_blocks=True,
)


CHAPTER_MD_TEMPLATE = """\
# {{ chapter_title }}

{% for section in sections %}
## {{ section.heading }}

{% for paragraph in section.paragraphs %}
{{ paragraph }}

{% endfor -%}
{% if section.materialized_code_blocks -%}
{% for code_block in section.materialized_code_blocks %}
{{ code_block }}

{% endfor -%}
{% endif -%}
{% if section.citations -%}
**Sources for this section:**

{% for c in section.citations -%}
- `{{ c.source_basename }}` — {{ c.claim }}
{% endfor %}

{% endif -%}
{% endfor %}
"""


CHALLENGES_MD_TEMPLATE = """\
# Active Recall Questions — {{ chapter_title }}

{% for q in challenges %}
{{ loop.index }}. {{ q }}
{% endfor %}
"""


# =============================================================================
# Section context preprocessing
# =============================================================================
def _basename(key: str) -> str:
    """Extract the last `/`-segment of a MinIO key. Robust to trailing
    slashes; falls back to the full key if no slash."""
    if not key:
        return ""
    return key.rstrip("/").rsplit("/", 1)[-1]


def build_section_context(
    section: dict,
    *,
    vault: dict[str, str],
    resolution_log: list[CodeRefResolution],
) -> dict:
    """Pre-process one sawc Section into the Jinja template context.

    Side effect: APPENDS one `CodeRefResolution` entry per code_ref
    to `resolution_log` (the caller's accumulator). This lets the
    caller compute audit stats in a single pass.

    Returns a dict with:
      - section_id
      - heading
      - paragraphs        (list[str])
      - materialized_code_blocks (list[str] — `fence_text` from vault)
      - citations         (list[{source_basename, claim}])
    """
    section_id = section.get("section_id", "?")
    heading = section.get("heading", "?")
    paragraphs = list(section.get("paragraphs") or [])

    materialized: list[str] = []
    for ref in (section.get("code_refs") or []):
        h = ref.get("hash") if isinstance(ref, dict) else None
        if not h:
            continue
        if h in vault:
            fence_text = vault[h]
            materialized.append(fence_text)
            # Re-hash + compare for the audit
            rehashed = _hash_block(fence_text)
            byte_drift = (rehashed != h)
            resolution_log.append(CodeRefResolution(
                hash=h,
                found_in_vault=True,
                byte_drift=byte_drift,
                materialized_chars=len(fence_text),
                section_id=section_id,
            ))
        else:
            resolution_log.append(CodeRefResolution(
                hash=h,
                found_in_vault=False,
                byte_drift=False,
                materialized_chars=0,
                section_id=section_id,
            ))

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
        "section_id":               section_id,
        "heading":                  heading,
        "paragraphs":               paragraphs,
        "materialized_code_blocks": materialized,
        "citations":                citations,
    }


# =============================================================================
# Rendering — three pure transforms
# =============================================================================
def render_chapter_md(
    chapter_title: str,
    sections_ctx: list[dict],
) -> str:
    """Render the README.md (full chapter markdown). Deterministic
    given identical inputs."""
    tpl = _JINJA_ENV.from_string(CHAPTER_MD_TEMPLATE)
    md = tpl.render(
        chapter_title=chapter_title,
        sections=sections_ctx,
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
    referenced_hashes: set[str] = {r.hash for r in resolution_log}
    n_total = len(resolution_log)
    n_resolved = sum(1 for r in resolution_log if r.found_in_vault)
    n_missing = sorted({r.hash for r in resolution_log if not r.found_in_vault})
    n_drift = sorted({r.hash for r in resolution_log if r.byte_drift})
    n_orphan = sorted(set(vault.keys()) - referenced_hashes)
    sentinels_left = len(_SENTINEL_RE.findall(rendered_chapter_md or ""))

    audit_passed = (
        not n_missing
        and not n_drift
        and sentinels_left == 0
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
