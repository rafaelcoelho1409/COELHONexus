"""Pure render helpers: section context build, dedup/align, chapter template rendering, vault merge, audit, and SHA hashing."""
from __future__ import annotations

import hashlib
import re

from .params import (
    DEDUP_MIN_CHARS,
    DEDUP_MIN_LINES,
    MISMATCH_MIN_CODE_IDENTS,
    NOISE_IDENTS,
    VAULT_HASH_LEN,
)
from .patterns import IDENT_RE, SENTINEL_RE
from .prompts import (
    CHAPTER_MD_TEMPLATE,
    JINJA_ENV,
)
from .schemas import AuditResult, CodeRefResolution


def _basename(key: str) -> str:
    """Extract the last `/`-segment of a MinIO key."""
    if not key:
        return ""
    return key.rstrip("/").rsplit("/", 1)[-1]


def _slugify(s: str) -> str:
    """Markdown-anchor-friendly slug for TOC links."""
    out = re.sub(r"[^a-zA-Z0-9\s-]", "", (s or "")).strip().lower()
    return re.sub(r"\s+", "-", out)


def build_section_context(
    section: dict,
    *,
    vault: dict[str, str],
    resolution_log: list[CodeRefResolution],
    normalized_hashes: set[str] | None = None,
) -> dict:
    """Build section template context from sawc Section. Appends CodeRefResolution per subtopic to resolution_log. normalized_hashes=hashes rewritten by LLM normalizer — treated as verbatim to suppress byte-drift false-positives."""
    # Lazy import to avoid a render→sawc_derive cycle.
    from ..sawc_derive.domain import python_ast_valid as _ast_valid

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
            ast_ok = _ast_valid(derived_code)
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
                hash = h or "",
                found_in_vault = False,
                byte_drift = False,
                materialized_chars = len(body),
                section_id = section_id,
                tier = tier,
            ))
        elif h:
            if h in vault:
                fence_text = vault[h]
                code_block = fence_text
                if normalized_hashes and h in normalized_hashes:
                    # Intentional LLM-normalizer rewrite — rehash will differ but it's not drift.
                    byte_drift = False
                    tier = "verbatim"
                else:
                    rehashed = hash_block(fence_text)
                    byte_drift = (rehashed != h)
                    tier = "hallucinated" if byte_drift else "verbatim"
                resolution_log.append(CodeRefResolution(
                    hash = h,
                    found_in_vault = True,
                    byte_drift = byte_drift,
                    materialized_chars = len(fence_text),
                    section_id = section_id,
                    tier = tier,
                ))
            else:
                resolution_log.append(CodeRefResolution(
                    hash = h,
                    found_in_vault = False,
                    byte_drift = False,
                    materialized_chars = 0,
                    section_id = section_id,
                    tier = "hallucinated",
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


def _code_inner(code_block: str) -> str:
    """Strip leading ```lang line and trailing ``` from a materialized code_block; returns inner body. '' if not fenced."""
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
    """Whitespace-collapsed body for duplicate detection."""
    return re.sub(r"\s+", " ", inner).strip()


def _idents(text: str) -> set[str]:
    return {w.lower() for w in IDENT_RE.findall(text or "")} - NOISE_IDENTS


def dedupe_and_align_sections(
    sections_ctx: list[dict],
    *,
    drop_mismatch: bool = True,
) -> dict:
    """Mutate sections_ctx in place; return {n_dedup, n_mismatch}. #1 cross-section body dedup (catches ~45% recycling that vault-hash dedup misses); #4 per-subtopic mismatch removal."""
    seen: dict[str, tuple[str, str, str]] = {}
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
                inner.count("\n") + 1 >= DEDUP_MIN_LINES
                or len(inner) >= DEDUP_MIN_CHARS
            )

            if nontrivial:
                key = _norm_body(inner)
                prev = seen.get(key)
                if prev and (prev[0], prev[1]) != (heading, subheading):
                    fh, fsub, fanchor = prev
                    ref = (
                        f"[**{fh} → {fsub}**](#{fanchor})"
                        if fanchor else f"**{fh} → {fsub}**"
                    )
                    sub["code_block"] = (
                        f"> _↳ Same code as {ref}; shown once, "
                        f"not repeated._"
                    )
                    sub["derived_caption"] = ""
                    n_dedup += 1
                    continue

            if drop_mismatch:
                ci = _idents(inner)
                if len(ci) >= MISMATCH_MIN_CODE_IDENTS:
                    ti = _idents(
                        subheading + " " + (sub.get("explanation") or ""),
                    )
                    if not (ci & ti):
                        sub["code_block"] = (
                            "> _(Code example omitted — it did not match "
                            "this subtopic and was likely misrouted.)_"
                        )
                        sub["derived_caption"] = ""
                        n_mismatch += 1
                        continue

            if nontrivial:
                seen.setdefault(
                    key, (heading, subheading, sub.get("anchor") or ""),
                )
    return {"n_dedup": n_dedup, "n_mismatch": n_mismatch}


def _build_toc(sections_ctx: list[dict]) -> list[dict]:
    """Nested TOC for the chapter template; omitted when fewer than 2 sections."""
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
    """Render full cookbook chapter markdown. Deterministic given identical inputs."""
    toc = _build_toc(sections_ctx) if len(sections_ctx) >= 2 else []
    tpl = JINJA_ENV.from_string(CHAPTER_MD_TEMPLATE)
    md = tpl.render(
        chapter_title = chapter_title,
        sections = sections_ctx,
        toc = toc,
    )
    md = re.sub(r"\n{4,}", "\n\n\n", md)
    return md.rstrip() + "\n"


def merge_vault_entries(per_source_manifests: list[dict]) -> dict[str, str]:
    """Merge per-source VaultManifest dicts into {hash: fence_text}. On collision the LAST source wins."""
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


def hash_block(payload: str, salt: int = 0) -> str:
    """16-hex SHA-256 prefix. MUST match `synth/vault.py:_hash_block`
    or the audit will spuriously fail."""
    seed = payload if salt == 0 else f"{payload}|{salt}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:VAULT_HASH_LEN]


def compute_audit(
    *,
    resolution_log: list[CodeRefResolution],
    vault: dict[str, str],
    rendered_chapter_md: str,
) -> AuditResult:
    """Aggregate per-ref resolution log into an AuditResult."""
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
    sentinels_left = len(SENTINEL_RE.findall(rendered_chapter_md or ""))

    n_verbatim = sum(1 for r in resolution_log if r.tier == "verbatim")
    n_derived = sum(1 for r in resolution_log if r.tier == "derived")
    n_hallucinated = sum(
        1 for r in resolution_log if r.tier == "hallucinated"
    )

    audit_passed = (
        not n_missing
        and not n_drift
        and sentinels_left == 0
        and n_hallucinated == 0
    )

    capped_details = resolution_log[:100]

    return AuditResult(
        n_code_refs_referenced = n_total,
        n_resolved = n_resolved,
        n_missing = n_missing,
        n_orphan_unused = n_orphan,
        n_byte_drift = n_drift,
        sentinels_in_output = sentinels_left,
        audit_passed = audit_passed,
        n_verbatim = n_verbatim,
        n_derived = n_derived,
        n_hallucinated = n_hallucinated,
        resolution_details = capped_details,
    )


def sha256_bytes(content: str) -> str:
    """Full 64-char SHA-256 of the content's UTF-8 bytes."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_manifest_hash(
    *,
    sawc_manifest_hash: str,
    mgsr_manifest_hash: str,
) -> str:
    from .versions import RENDER_SCHEMA_VERSION, RENDER_TEMPLATE_VERSION
    payload = (
        f"sawc={sawc_manifest_hash}|"
        f"mgsr={mgsr_manifest_hash}|"
        f"template={RENDER_TEMPLATE_VERSION}|"
        f"schema={RENDER_SCHEMA_VERSION}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_render_payload(text: str) -> dict:
    """Parse the persisted render-latest.json blob."""
    return json.loads(text)
