"""Render + audit + persist orchestrator for one chapter.

Pure deterministic transform + cryptographic vault round-trip audit
for the materialization stage. The byte-exact guarantee (vs arXiv
2601.03640's literal-payload failure mode) holds because the SAWC
writer NEVER copies code — vault sentinels hide fenced blocks until
this node resolves them.

LLM-touched stage (2026-06-08): pre-render, every code block in the
loaded vault goes through a single bandit-routed normalization call
that fixes indentation / line-break / whitespace drift introduced by
upstream ingestion (e.g. Mintlify MDX flattening that strips function
body indent). Results are cached per-content-hash in MinIO so each
unique block is normalized exactly once — re-renders are free.
Plain-text / output-only blocks bypass the LLM entirely.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import time

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState

from .domain import (
    build_section_context,
    compute_audit,
    compute_manifest_hash,
    dedupe_and_align_sections,
    merge_vault_entries,
    render_chapter_md,
    sha256_bytes,
)
from .keys import (
    artifact_key,
    latest_blob_key,
    mgsr_latest_key,
    planner_latest_key,
    sawc_latest_key,
    source_key_to_vault_key,
    versioned_blob_key,
)
from .schemas import CodeRefResolution, RenderedArtifact, RenderResult
from .versions import RENDER_TEMPLATE_VERSION


logger = logging.getLogger(__name__)


# ============================================================
# LLM code-block normalizer (2026-06-08)
# ============================================================
# Pass each vault entry's fence_text through a bandit-routed LLM that
# fixes formatting drift (broken indentation, MDX-mangled brackets,
# stripped whitespace, etc.) without changing identifier names, string
# literals, or any non-whitespace content. Cached per content-hash:
#   synth-vault/{slug}/normalized/v1/{vault_hash}.txt
# so the cost is one-shot per unique block. Bump the version segment
# (v1/) to invalidate everything when the prompt changes.

_NORMALIZE_PROMPT_VERSION = "v3-2026-06-08"
_NORMALIZE_CACHE_PREFIX = "synth-vault/{slug}/normalized/" + _NORMALIZE_PROMPT_VERSION

# Languages where the LLM call is skipped — content is either plain
# text (no formatting concept) or terminal output (whitespace is
# semantic and any reformat is wrong).
_SKIP_LANGS = frozenset({
    "", "text", "plaintext", "txt", "output", "console",
    "log", "json", "yaml", "yml", "xml", "diff", "patch",
    "mermaid", "ansi",
})

# Python-family languages get an extra AST-validation pass. Python's
# `ast` is stdlib (no extra deps) and the highest-signal failure mode
# we observed in ch-01 (Mintlify MDX-flattened bodies stripping the
# function-body indent). On AST fail we retry the LLM call with a
# sharper prompt; on a second failure we keep the LLM's best attempt.
_PYTHON_LANGS = frozenset({"python", "py", "py3", "python3"})


def _normalize_cache_key(slug: str, vault_hash: str) -> str:
    return (_NORMALIZE_CACHE_PREFIX.format(slug = slug) +
            "/" + vault_hash + ".txt")


def _strip_code_fences(s: str) -> str:
    """If the LLM returned a fenced block (or just a trailing close
    marker) despite the instruction, peel the leading and trailing
    fence lines INDEPENDENTLY.

    The earlier version only stripped a trailing fence when a leading
    fence existed — so a response of ``body\\n``` produced a fence
    marker that bled into the surrounding markdown and rendered as a
    stray empty code block (observed in ch-02-configuration line 62
    on the 2026-06-08 browser-use run).
    """
    s = (s or "").strip("\n")
    if not s:
        return s
    lines = s.split("\n")
    if lines and lines[0].lstrip().startswith(("```", "~~~")):
        lines = lines[1:]
    if lines and lines[-1].lstrip().startswith(("```", "~~~")):
        lines = lines[:-1]
    return "\n".join(lines)


# Inner-fence extractor: when the LLM ignores the "NO commentary, NO
# fences" instruction and returns prose + a fenced code block (e.g.
# `"The provided code is already correct.\n```bash\n<code>\n```"`),
# pull out the LARGEST fenced block's body. Observed in ch-03 line 242
# of the browser-use 2026-06-08 re-run.
_INNER_FENCE_RE = re.compile(
    r'(?P<open>```+|~~~+)(?P<info>[^\n]*)\n(?P<body>.*?)\n(?P=open)',
    re.DOTALL,
)


def _extract_largest_fenced_body(s: str) -> str | None:
    """Find every fenced block in `s` and return the body of the
    longest one. Used as a recovery path when `_strip_code_fences`
    leaves residual fence markers (= the LLM returned commentary +
    a fenced code block inside it)."""
    candidates = [m.group("body") for m in _INNER_FENCE_RE.finditer(s)]
    if not candidates:
        return None
    return max(candidates, key = len)


# Fence parser — fence_text values in the merged vault are full fenced
# blocks (``` + info-string + body + ```). We split, normalize ONLY the
# body, and reassemble — so the language info-string and the fence
# markers are byte-preserved no matter what the LLM does.
_FENCE_RE = re.compile(
    r'^(?P<open>```+|~~~+)(?P<info>[^\n]*)\n'
    r'(?P<body>.*?)'
    r'\n(?P<close>```+|~~~+)\s*$',
    re.DOTALL,
)


def _split_fence(fence_text: str):
    """Return (open_marker, info_string, body, close_marker) or None when
    the value doesn't look like a fenced block (defensive — handles
    runtime-sentinelized entries that might be raw bodies)."""
    if not fence_text:
        return None
    m = _FENCE_RE.match(fence_text.strip("\n"))
    if not m:
        return None
    return (m.group("open"), m.group("info"),
            m.group("body"), m.group("close"))


def _lang_from_info(info_string: str) -> str:
    """First whitespace-separated token of the info-string is the lang
    hint (e.g. `python theme={...}` → "python")."""
    info = (info_string or "").strip()
    return info.split()[0].lower() if info else ""


_NORMALIZE_PROMPT_BASE = (
    "You are a code formatter. Fix indentation, line-break, and "
    "whitespace so the {lang} code below is correctly formatted. The "
    "code was likely mangled by upstream tooling that flattened "
    "indentation (Mintlify MDX export, HTML-rendered copy-paste, etc.) "
    "— EVERY continuation that's supposed to be nested often ends up at "
    "column 0.\n\n"
    "Look for and fix THESE common failure modes (assume they're "
    "present unless you can confirm otherwise):\n"
    "1. Function / class / if / for / while / try / with bodies sitting "
    "at the SAME column as their `def`/`class`/etc. header — must be "
    "MORE indented (Python: 4 spaces deeper). This is a hard syntax "
    "error.\n"
    "2. Keyword arguments inside a function CALL `foo(` ... `)` sitting "
    "at column 0 — must be indented one level deeper than the opening "
    "`(`. This parses but is unreadable and wrong style.\n"
    "3. Items inside `[...]` / `{{...}}` literals sitting at column 0 "
    "— same rule: indent one level deeper than the opening bracket.\n"
    "4. Method chains, conditional expressions, and `return` "
    "continuations broken across lines but flattened to column 0 — "
    "indent the continuation.\n\n"
    "Strict rules:\n"
    "- Preserve every identifier, string literal, number, operator, "
    "comment, and language keyword BYTE-EXACT. Only whitespace may "
    "change. NEVER rename, NEVER reorder, NEVER add or remove tokens.\n"
    "- Use 4-space indents for Python; match the original style for "
    "other languages.\n"
    "- If the code is genuinely already correct, return it unchanged.\n"
    "- Return ONLY the fixed code. NO fences, NO commentary, NO "
    "preamble, NO explanation.\n\n"
    "```{lang}\n{body}\n```"
)

_NORMALIZE_PROMPT_PYTHON_RETRY = (
    "The Python code below failed to parse with `ast.parse` — likely "
    "because function or class bodies are at the same indent level as "
    "their `def`/`class` header (Mintlify MDX flattening). Fix the "
    "indentation so every `def`/`class`/`if`/`for`/`while`/`try`/"
    "`with`/`async def` block has its body indented at least 4 spaces "
    "deeper than the header. Preserve every non-whitespace character "
    "byte-exact. Parser error: {error}\n\n"
    "Return ONLY the fixed code with NO fences, NO commentary, NO "
    "preamble.\n\n"
    "```python\n{body}\n```"
)


def _python_ast_valid(body: str) -> tuple[bool, str]:
    """Return (ok, error_message_or_empty). Uses stdlib `ast` so this
    adds no dep. Treated as the truth source for python/py blocks
    because the normalizer's biggest observed failure mode is
    unindented function/class bodies."""
    try:
        ast.parse(body)
        return True, ""
    except SyntaxError as e:
        return False, f"line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _llm_normalize_body(
    *, body: str, lang: str, prompt: str,
) -> str | None:
    """Single LLM call. Returns the fence-stripped response body, or
    None on failure / empty output. Cheap helper so the AST retry path
    can re-call without duplicating error handling.

    Robust against prompt-following failures (2026-06-08): some
    free-tier models return commentary + a fenced code block instead of
    raw code. We first strip outer fences, then — if the result still
    contains fence markers — pull out the largest inner fenced block's
    body. If neither works (commentary with NO code block) we reject
    the response so the caller falls back to the original."""
    try:
        response, _meta = await chat_judge_bandit_async(
            prompt,
            max_tokens = min(8000, max(512, 2 * len(body))),
            temperature = 0.0,
            timeout_s = 60.0,
            dd_process = "dd-grader",
        )
    except Exception as e:
        logger.warning(
            f"[render-normalize] LLM call failed lang={lang!r}: "
            f"{type(e).__name__}: {e}"
        )
        return None

    fixed = _strip_code_fences(response or "")

    # Recovery path: residual ``` / ~~~ markers mean the LLM nested a
    # fenced code block inside its response (often after a "this looks
    # correct" preamble). Pull the largest inner block's body.
    if "```" in fixed or "~~~" in fixed:
        inner = _extract_largest_fenced_body(response or "")
        if inner and inner.strip():
            fixed = inner
        else:
            logger.info(
                f"[render-normalize] response had residual fence "
                f"markers + no extractable inner block (lang={lang!r}) "
                f"— rejecting (caller keeps original)"
            )
            return None

    return fixed if fixed.strip() else None


async def _normalize_code_block(
    *,
    minio,
    slug: str,
    vault_hash: str,
    fence_text: str,
) -> tuple[str, bool]:
    """Send one vault entry through the rotator to fix any formatting
    drift in its code body. Returns `(fence_text, was_modified)`.
    `was_modified=True` means the body was intentionally rewritten — the
    caller uses this to suppress the byte-drift audit check for that
    hash (otherwise every normalize trips a false-positive drift). Falls
    back to the original on ANY error; `was_modified=False` then."""
    if not fence_text or len(fence_text) > 12_000:
        # Empty / oversized — skip (cache blow-out + diminishing returns).
        return fence_text, False

    parts = _split_fence(fence_text)
    if parts is None:
        return fence_text, False
    open_marker, info_string, body, close_marker = parts
    lang = _lang_from_info(info_string)
    if lang in _SKIP_LANGS:
        return fence_text, False
    if not body.strip():
        return fence_text, False

    cache_key = _normalize_cache_key(slug, vault_hash)
    try:
        if await minio.exists(cache_key):
            cached = await minio.read_text(cache_key)
            return cached, (cached != fence_text)
    except Exception as e:
        logger.warning(
            f"[render-normalize] cache read failed for {cache_key!r}: "
            f"{type(e).__name__}: {e}"
        )

    fixed_body = await _llm_normalize_body(
        body = body, lang = lang,
        prompt = _NORMALIZE_PROMPT_BASE.format(
            lang = lang or "code", body = body,
        ),
    )
    if fixed_body is None:
        return fence_text, False

    # Python-specific AST validation: if the first-pass response still
    # fails to parse, re-prompt with the parser error so the model can
    # target the exact failure. This rescues the Mintlify-flatten case
    # where small free-tier models read the body as two valid statements
    # at top level and return it unchanged.
    if lang in _PYTHON_LANGS:
        ok, err = _python_ast_valid(fixed_body)
        if not ok:
            retry = await _llm_normalize_body(
                body = body, lang = lang,
                prompt = _NORMALIZE_PROMPT_PYTHON_RETRY.format(
                    body = fixed_body, error = err,
                ),
            )
            if retry is not None:
                retry_ok, _ = _python_ast_valid(retry)
                if retry_ok or len(retry) >= len(fixed_body):
                    fixed_body = retry
            else:
                logger.info(
                    f"[render-normalize] hash={vault_hash} python AST "
                    f"still invalid after retry ({err}) — keeping "
                    f"best-effort output"
                )

    fixed = f"{open_marker}{info_string}\n{fixed_body}\n{close_marker}"
    try:
        await minio.write(
            cache_key, fixed, content_type = "text/plain; charset=utf-8",
        )
    except Exception as e:
        logger.warning(
            f"[render-normalize] cache write failed for {cache_key!r}: "
            f"{type(e).__name__}: {e}"
        )
    return fixed, (fixed != fence_text)


async def _normalize_vault_codes(
    *,
    minio,
    slug: str,
    vault: dict,
) -> tuple[dict, set[str], int, int]:
    """Apply `_normalize_code_block` to every vault entry in parallel.
    The merged vault is `dict[str, str]` (hash → fence_text) per
    `merge_vault_entries`. Returns `(modified_vault, normalized_hashes,
    n_normalized, n_skipped)`. `normalized_hashes` is the set of vault
    hashes whose bodies were intentionally rewritten — callers pass it
    to `build_section_context` so byte-drift detection treats them as
    `verbatim` rather than `hallucinated`. Mutates the input dict in
    place (callers get back the same reference for convenience)."""
    if not vault:
        return vault, set(), 0, 0

    items = list(vault.items())
    coros = [
        _normalize_code_block(
            minio = minio, slug = slug,
            vault_hash = h,
            fence_text = ft if isinstance(ft, str) else str(ft or ""),
        )
        for h, ft in items
    ]
    results = await asyncio.gather(*coros, return_exceptions = True)

    normalized_hashes: set[str] = set()
    n_norm = 0
    n_skip = 0
    for (h, orig), result in zip(items, results):
        if isinstance(result, Exception):
            logger.warning(
                f"[render-normalize] task failed for hash={h}: "
                f"{type(result).__name__}: {result}"
            )
            n_skip += 1
            continue
        new_text, was_modified = result
        if not was_modified:
            n_skip += 1
            continue
        vault[h] = new_text
        normalized_hashes.add(h)
        n_norm += 1
    return vault, normalized_hashes, n_norm, n_skip


async def _load_per_source_vaults(
    minio,
    slug: str,
    source_keys: list[str],
) -> tuple[dict[str, str], int, int]:
    """Load + merge per-source vault manifests.
    Returns (merged_vault, n_loaded, n_skipped_missing).

    CRITICAL FIX 2026-05-24 (lost in the 2026-06-05 cosmic-python refactor
    bd98674, restored 2026-06-08): when a per-source vault file doesn't
    exist on MinIO (common when ingestion built only one consolidated
    `llms-full` vault for the whole corpus), fall back to runtime
    sentinelization of the raw ingestion page. Without this fallback,
    render's audit reports `n_resolved=0, n_missing=N` for every
    code_ref the LLM cited and the final chapter has ZERO code blocks
    despite the SAWC output containing valid hashes."""
    from ..vault.domain import sentinelize_doc as _sentinelize_doc

    manifests: list[dict] = []
    n_skipped = 0
    n_runtime = 0
    for source_key in source_keys:
        vault_key = source_key_to_vault_key(source_key, slug)
        if await minio.exists(vault_key):
            try:
                text = await minio.read_text(vault_key)
                manifests.append(json.loads(text))
                continue
            except Exception as e:
                logger.warning(
                    f"[render_audit_write] vault {vault_key!r} unreadable: "
                    f"{type(e).__name__}: {e} — falling back to runtime"
                )

        # Runtime fallback: read raw ingestion page + sentinelize on-the-fly.
        try:
            raw = await minio.read_text(source_key)
            if not raw or "<code-ref hash=" in raw:
                n_skipped += 1
                continue
            _, entries = _sentinelize_doc(raw)
            if entries:
                # Convert VaultEntry objects → manifest dict shape that
                # merge_vault_entries expects (entries dict keyed by hash).
                manifests.append({
                    "entries": {
                        h: (e.model_dump() if hasattr(e, "model_dump") else dict(e))
                        for h, e in entries.items()
                    },
                })
                n_runtime += 1
            else:
                n_skipped += 1
        except Exception as e:
            n_skipped += 1
            logger.warning(
                f"[render_audit_write] runtime-sentinelize failed for "
                f"{source_key!r}: {type(e).__name__}: {e}"
            )

    merged = merge_vault_entries(manifests)
    if n_runtime:
        logger.info(
            f"[render_audit_write] {slug}: runtime-sentinelized "
            f"{n_runtime} sources at vault-load time (no pre-built "
            f"vaults found); merged vault has {len(merged)} entries total"
        )
    return merged, len(manifests), n_skipped


async def _verify_cache_hit_artifacts(
    minio,
    slug: str,
    chapter_id: str,
    artifacts: list[dict],
) -> bool:
    """Cache hit only valid when all 3 content artifacts also exist —
    defense against partial-write crash state."""
    for art in artifacts:
        key = art.get("minio_key") or ""
        if not key or not await minio.exists(key):
            return False
    return True


async def render_audit_write_run(state: SynthState) -> dict:
    """Render + audit + persist for one chapter. Zero LLM calls."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped": "no_slug_or_chapter_id", "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    sawc_key = sawc_latest_key(slug, chapter_id)
    mgsr_key = mgsr_latest_key(slug, chapter_id)

    if not await minio.exists(sawc_key):
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped":  "sawc_not_found",
                "sawc_key": sawc_key,
                "wall_ms":  int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc {sawc_key!r} not in MinIO — run sawc_write first",
        }
    if not await minio.exists(mgsr_key):
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped":  "mgsr_not_found",
                "mgsr_key": mgsr_key,
                "wall_ms":  int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"mgsr {mgsr_key!r} not in MinIO — run mgsr_replan first",
        }

    try:
        sawc_text = await minio.read_text(sawc_key)
        sawc = json.loads(sawc_text)
        mgsr_text = await minio.read_text(mgsr_key)
        mgsr = json.loads(mgsr_text)
    except Exception as e:
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc/mgsr unreadable: {type(e).__name__}: {e}",
        }

    # v1 doesn't loop; abort cleanly if mgsr didn't halt (shouldn't happen).
    mgsr_decision = (mgsr or {}).get("decision") or {}
    if not mgsr_decision.get("halt", True):
        return {
            "chapter_path":  "",
            "chapter_stats": {
                "skipped":     "mgsr_not_halted",
                "halt_reason": mgsr_decision.get("halt_reason"),
                "wall_ms":     int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  (
                "mgsr_replan says halt = false (v2 loop required); v1 "
                "doesn't loop back to sawc yet"
            ),
        }

    chapter_title = sawc.get("chapter_title") or chapter_id
    sections = sawc.get("sections") or []
    sawc_manifest_hash = sawc.get("sawc_manifest_hash") or ""
    mgsr_manifest_hash = mgsr.get("mgsr_manifest_hash") or ""

    await emit_progress(
        thread_id, "render_audit_write", "start",
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_sections = len(sections),
        mgsr_halt = mgsr_decision.get("halt", True),
        mgsr_halt_reason = mgsr_decision.get("halt_reason", "?"),
    )

    manifest_hash = compute_manifest_hash(
        sawc_manifest_hash = sawc_manifest_hash,
        mgsr_manifest_hash = mgsr_manifest_hash,
    )
    versioned_key = versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            arts = cached.get("artifacts") or []
            if await _verify_cache_hit_artifacts(minio, slug, chapter_id, arts):
                audit = cached.get("audit") or {}
                elapsed = int((time.monotonic() - t0) * 1000)
                readme_key = artifact_key(slug, chapter_id, "README.md")
                stats = {
                    "audit_passed":         audit.get("audit_passed", False),
                    "n_artifacts":          len(arts),
                    "n_code_refs":          audit.get("n_code_refs_referenced", 0),
                    "n_resolved":           audit.get("n_resolved", 0),
                    "n_missing":            len(audit.get("n_missing") or []),
                    "n_byte_drift":         len(audit.get("n_byte_drift") or []),
                    "sentinels_in_output":  audit.get("sentinels_in_output", 0),
                    "rendered_chars":       cached.get("rendered_chars", 0),
                    "wall_ms":              elapsed,
                    "store_path":           latest_key,
                    "versioned_path":       versioned_key,
                    "readme_path":          readme_key,
                    "manifest_hash":        manifest_hash,
                    "cache_hit":            True,
                    "template_version":     cached.get("template_version"),
                }
                await emit_progress(
                    thread_id, "render_audit_write", "done",
                    audit_passed = stats["audit_passed"],
                    n_artifacts = stats["n_artifacts"],
                    n_code_refs = stats["n_code_refs"],
                    n_resolved = stats["n_resolved"],
                    n_missing = stats["n_missing"],
                    n_byte_drift = stats["n_byte_drift"],
                    sentinels_in_output = stats["sentinels_in_output"],
                    rendered_chars = stats["rendered_chars"],
                    wall_ms = elapsed, cache_hit = True,
                )
                logger.info(
                    f"[render_audit_write] {slug}/{chapter_id}: CACHE HIT — "
                    f"audit_passed = {stats['audit_passed']}, "
                    f"refs = {stats['n_resolved']}/{stats['n_code_refs']}, "
                    f"{elapsed} ms"
                )
                return {"chapter_path": readme_key, "chapter_stats": stats}
            else:
                logger.warning(
                    f"[render_audit_write] cached render_result exists but "
                    f"artifacts missing — re-rendering"
                )
        except Exception as e:
            logger.warning(
                f"[render_audit_write] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    plan_key = planner_latest_key(slug)
    source_keys: list[str] = []
    if await minio.exists(plan_key):
        try:
            plan_text = await minio.read_text(plan_key)
            plan = json.loads(plan_text)
            for ch in (plan.get("chapters") or []):
                if (ch or {}).get("id") == chapter_id:
                    source_keys = sorted(ch.get("sources") or [])
                    break
        except Exception as e:
            logger.warning(
                f"[render_audit_write] plan {plan_key!r} unreadable: "
                f"{type(e).__name__}: {e}"
            )

    vault, n_loaded, n_skipped = await _load_per_source_vaults(
        minio, slug, source_keys,
    )
    # LLM normalize pass — fixes indentation / whitespace drift from
    # upstream ingestion (Mintlify MDX flatten, MDX-mangled brackets,
    # smart quotes, stripped \\t, etc.). One bandit call per UNIQUE
    # vault entry, cached per-content-hash so re-renders are free.
    # Failures fall through to the original byte-exact text — never
    # blocks the render.
    vault, normalized_hashes, n_norm, n_skip_norm = await _normalize_vault_codes(
        minio = minio, slug = slug, vault = vault,
    )
    await emit_progress(
        thread_id, "render_audit_write", "inputs_loaded",
        n_sources = len(source_keys),
        n_vault_files_loaded = n_loaded,
        n_vault_files_skipped = n_skipped,
        n_vault_entries = len(vault),
        n_codes_normalized = n_norm,
        n_codes_unchanged = n_skip_norm,
    )

    resolution_log: list[CodeRefResolution] = []
    sections_ctx = [
        build_section_context(
            s, vault = vault, resolution_log = resolution_log,
            normalized_hashes = normalized_hashes,
        )
        for s in sections
    ]
    # Write-path quality pass (DD-SYNTH-SECTION-RECYCLING-2026-05-29
    # fixes #1 + #4): cross-reference within-chapter recycled code
    # blocks + omit misrouted ones. Audit-safe — only rewrites
    # code_block strings, so resolution_log/sentinel counts stay
    # consistent.
    dedup_stats = dedupe_and_align_sections(
        sections_ctx,
        drop_mismatch = os.environ.get(
            "KD_RENDER_DROP_MISMATCH", "true",
        ).lower() not in ("0", "false", "no"),
    )
    if dedup_stats["n_dedup"] or dedup_stats["n_mismatch"]:
        logger.info(
            f"[render_audit_write] {slug}/{chapter_id}: write-path pass — "
            f"{dedup_stats['n_dedup']} recycled code block(s) cross-referenced, "
            f"{dedup_stats['n_mismatch']} misrouted block(s) omitted"
        )

    # v2 cookbook (matches RenderResult schema): subtopics replaced
    # legacy paragraphs. RenderResult declares `n_subtopics_total`;
    # passing the legacy `n_paragraphs_total` name to it raises
    # `pydantic.ValidationError: n_subtopics_total field required`.
    n_subtopics_total = sum(len(s.get("subtopics") or []) for s in sections)
    n_citations_total = sum(len(s.get("citations") or []) for s in sections)

    chapter_md = render_chapter_md(chapter_title, sections_ctx)

    # Audit AFTER rendering — sentinels_in_output is measured on the rendered MD.
    audit = compute_audit(
        resolution_log = resolution_log,
        vault = vault,
        rendered_chapter_md = chapter_md,
    )

    # CONTENT-PRESENT GATE (DD-SYNTH-PROSE-PATH-2026-05-30 fix #2). A
    # section the SAWC writer couldn't draft renders as an empty
    # placeholder (0 subtopics). The byte-exact vault audit can't see
    # this — a placeholder has no code refs, so it PASSES audit while
    # being empty (this masked LangFuse ch-07/ch-08). Count placeholder
    # sections and FAIL the audit when any exist, so the chapter
    # surfaces as not-ready (Study sidebar reads audit_passed) instead
    # of silently shipping blank.
    n_placeholder_sections = sum(
        1 for s in sections_ctx if not (s.get("subtopics"))
    )
    if n_placeholder_sections:
        audit = audit.model_copy(update = {"audit_passed": False})
        logger.warning(
            f"[render_audit_write] {slug}/{chapter_id}: "
            f"{n_placeholder_sections}/{len(sections_ctx)} section(s) are EMPTY "
            f"placeholders (writer produced 0 subtopics) — failing audit. The "
            f"prose path should prevent this; investigate the section's sources."
        )

    await emit_progress(
        thread_id, "render_audit_write", "rendered",
        chapter_chars = len(chapter_md),
        n_sections_rendered = len(sections_ctx),
        n_placeholder_sections = n_placeholder_sections,
        n_code_refs_resolved = audit.n_resolved,
        n_code_refs_missing = len(audit.n_missing),
        n_code_refs_drift = len(audit.n_byte_drift),
        sentinels_in_output = audit.sentinels_in_output,
        audit_passed = audit.audit_passed,
        n_code_deduped = dedup_stats["n_dedup"],
        n_code_mismatch_omitted = dedup_stats["n_mismatch"],
    )

    readme_key = artifact_key(slug, chapter_id, "README.md")

    await minio.write(
        readme_key, chapter_md,
        content_type = "text/markdown; charset=utf-8",
    )

    artifacts = [
        RenderedArtifact(
            name = "README.md",
            minio_key = readme_key,
            size_bytes = len(chapter_md.encode("utf-8")),
            sha256 = sha256_bytes(chapter_md),
        ),
    ]

    await emit_progress(
        thread_id, "render_audit_write", "artifacts_written",
        n_artifacts = len(artifacts),
        total_bytes = sum(a.size_bytes for a in artifacts),
        artifact_names = [a.name for a in artifacts],
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    result = RenderResult(
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework_slug = slug,
        artifacts = artifacts,
        audit = audit,
        rendered_chars = len(chapter_md),
        n_sections = len(sections),
        n_subtopics_total = n_subtopics_total,
        n_citations_total = n_citations_total,
        sawc_manifest_hash = sawc_manifest_hash,
        mgsr_manifest_hash = mgsr_manifest_hash,
        render_manifest_hash = manifest_hash,
        wall_ms = elapsed,
        # Persist the per-chapter synth thread so the Study chapter strip
        # can re-open the chapter's LangGraph canvas after a page refresh.
        # The schema declares this field with a `""` default; without
        # passing it explicitly, the chapters API returns thread_id=None
        # and clicking a (done) chapter cell falls into the "no thread"
        # branch — graph nodes never repaint.
        thread_id = thread_id,
    )
    payload = result.model_dump()
    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(
        versioned_key, blob_bytes, content_type = "application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type = "application/json",
    )

    stats = {
        "audit_passed":          audit.audit_passed,
        "n_artifacts":           len(artifacts),
        "n_code_refs":           audit.n_code_refs_referenced,
        "n_resolved":            audit.n_resolved,
        "n_missing":             len(audit.n_missing),
        "n_byte_drift":          len(audit.n_byte_drift),
        "n_orphan_unused":       len(audit.n_orphan_unused),
        "sentinels_in_output":   audit.sentinels_in_output,
        "rendered_chars":        len(chapter_md),
        "n_sections":            len(sections),
        "n_subtopics_total":    n_subtopics_total,
        "n_citations_total":     n_citations_total,
        "n_vault_files_loaded":  n_loaded,
        "n_vault_files_skipped": n_skipped,
        "n_vault_entries":       len(vault),
        "wall_ms":               elapsed,
        "store_path":            latest_key,
        "versioned_path":        versioned_key,
        "readme_path":           readme_key,
        "manifest_hash":         manifest_hash,
        "cache_hit":             False,
        "template_version":      RENDER_TEMPLATE_VERSION,
    }
    await emit_progress(
        thread_id, "render_audit_write", "done",
        audit_passed = audit.audit_passed,
        n_artifacts = len(artifacts),
        n_code_refs = audit.n_code_refs_referenced,
        n_resolved = audit.n_resolved,
        n_missing = len(audit.n_missing),
        n_byte_drift = len(audit.n_byte_drift),
        sentinels_in_output = audit.sentinels_in_output,
        rendered_chars = len(chapter_md),
        wall_ms = elapsed,
    )
    logger.info(
        f"[render_audit_write] {slug}/{chapter_id}: "
        f"audit_passed = {audit.audit_passed}, "
        f"{audit.n_resolved}/{audit.n_code_refs_referenced} code_refs "
        f"resolved, {len(audit.n_missing)} missing, "
        f"{len(audit.n_byte_drift)} drift, "
        f"{audit.sentinels_in_output} sentinels left; "
        f"3 artifacts written ({sum(a.size_bytes for a in artifacts)} bytes); "
        f"{elapsed} ms"
    )
    state_status = "audit_failed" if not audit.audit_passed else None
    state_patch = {"chapter_path": readme_key, "chapter_stats": stats}
    if state_status:
        state_patch["status"] = state_status
        state_patch["error"] = (
            f"render audit failed: missing = {len(audit.n_missing)} "
            f"drift = {len(audit.n_byte_drift)} "
            f"unresolved_sentinels = {audit.sentinels_in_output}"
        )
    return state_patch
