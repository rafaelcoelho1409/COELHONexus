"""doc_distill — produce a compact semantic representation per doc.

Pipeline:
  1. Load relevant_files (post-off_topic).
  2. If N ≤ _PASS_THROUGH_THRESHOLD: skip (downstream uses raw bodies).
  3. Else: parallel LLM call per doc → {summary, key_terms}.
     - Pydantic-validated, repair loop on fail.
     - Response format json_schema enforced where supported.
  4. Persist as MinIO JSON (content-addressed + latest pointer).

State writes:
  doc_distill_ref   — MinIO key of the JSON ({key → DocDistillate, ...})
  doc_distill_stats — counts + cache_hit + wall_ms
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from hashlib import sha256
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState

from .constants import (
    _BLOB_PREFIX,
    _BODY_CHARS_MAX,
    _CONCURRENCY,
    _KEY_TERMS_MAX,
    _KEY_TERMS_MIN,
    _KEY_TERM_CHARS_MAX,
    _KEY_TERM_CHARS_MIN,
    _MAX_REPAIR_ATTEMPTS,
    _MAX_TOKENS,
    _PASS_THROUGH_THRESHOLD,
    _PROMPT_VERSION,
    _SUMMARY_WORDS_MAX,
    _SUMMARY_WORDS_MIN,
    _TEMPERATURE,
)


logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# -------------------------------------------------------------- #
# Pydantic schema                                                 #
# -------------------------------------------------------------- #
class DocDistillate(BaseModel):
    """Per-doc semantic representation for the LLM-first planner."""
    summary: str = Field(
        description=(
            f"{_SUMMARY_WORDS_MIN}-{_SUMMARY_WORDS_MAX} words. ONE "
            f"sentence describing what THIS file teaches/documents."
        ),
    )
    key_terms: list[str] = Field(
        description=(
            f"{_KEY_TERMS_MIN}-{_KEY_TERMS_MAX} technical identifiers "
            f"(function/class names, CLI commands, config keys, type "
            f"names) that appear in this doc."
        ),
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        n = len(s.split())
        if not (_SUMMARY_WORDS_MIN <= n <= _SUMMARY_WORDS_MAX):
            raise ValueError(
                f"summary must be {_SUMMARY_WORDS_MIN}-"
                f"{_SUMMARY_WORDS_MAX} words; got {n}"
            )
        return s

    @field_validator("key_terms")
    @classmethod
    def _validate_terms(cls, v: list[str]) -> list[str]:
        if not (_KEY_TERMS_MIN <= len(v) <= _KEY_TERMS_MAX):
            raise ValueError(
                f"key_terms count must be {_KEY_TERMS_MIN}-"
                f"{_KEY_TERMS_MAX}; got {len(v)}"
            )
        out: list[str] = []
        seen: set[str] = set()
        for t in v:
            s = " ".join(t.strip().split())
            if not (_KEY_TERM_CHARS_MIN <= len(s) <= _KEY_TERM_CHARS_MAX):
                raise ValueError(
                    f"key_term length must be {_KEY_TERM_CHARS_MIN}-"
                    f"{_KEY_TERM_CHARS_MAX}; got {len(s)}"
                )
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
        if len(out) < _KEY_TERMS_MIN:
            raise ValueError(
                f"after dedup, only {len(out)} unique key_terms "
                f"(minimum {_KEY_TERMS_MIN})"
            )
        return out


_DISTILL_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "doc_distillate",
        "schema": DocDistillate.model_json_schema(),
        "strict": False,
    },
}


def _build_prompt(framework: str, source_key: str, body: str) -> str:
    # V6 (2026-05-28) — prompt-prefix reordering for KV cache hits across
    # Groq + Gemini implicit + DeepSeek + NIM. Static rubric/schema
    # FIRST (cacheable prefix shared by all 135+ calls in a corpus),
    # dynamic file content LAST. Per-arm KV reuse yields 2-3x TTFT
    # improvement after warmup on providers that auto-cache.
    return (
        # ── STATIC PREFIX (KV-cacheable across all calls in this corpus) ──
        f"You are summarizing ONE documentation file from the "
        f"{framework} corpus for use in chapter planning.\n\n"
        f"OUTPUT FORMAT — STRICT JSON:\n"
        f"{{\n"
        f'  "summary":   "ONE sentence ({_SUMMARY_WORDS_MIN}-'
        f'{_SUMMARY_WORDS_MAX} words) — what does THIS file teach? '
        f'Name the specific feature/command/concept. Avoid generic '
        f'framing.",\n'
        f'  "key_terms": ["term1", ..., "termN"]  /* '
        f'{_KEY_TERMS_MIN}-{_KEY_TERMS_MAX} technical identifiers '
        f'visible in the file: function names, class names, CLI '
        f'subcommands, config keys, type names. NOT generic words '
        f'like "function" or "configuration". */\n'
        f"}}\n\n"
        f"Respond ONLY with valid JSON. No prose, no markdown wrap.\n\n"
        # ── DYNAMIC SUFFIX (changes per call) ──
        f"FILE: {source_key}\n\n"
        f"--- FILE CONTENT ---\n"
        f"{body[:_BODY_CHARS_MAX]}\n"
        f"--- END FILE ---"
    )


def _parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _try_validate(d: dict) -> tuple[Optional[DocDistillate], Optional[str]]:
    try:
        return DocDistillate.model_validate(d), None
    except Exception as e:
        return None, str(e)[:200]


# -------------------------------------------------------------- #
# Per-doc worker                                                  #
# -------------------------------------------------------------- #
async def _distill_one(
    sem: asyncio.Semaphore,
    minio,
    framework: str,
    source_key: str,
) -> tuple[str, Optional[DocDistillate], int]:
    """Returns (source_key, distillate or None, wall_ms)."""
    async with sem:
        t0 = time.monotonic()
        try:
            body = await minio.read_text(source_key)
        except Exception as e:
            logger.warning(
                f"[doc_distill] failed to read {source_key}: "
                f"{type(e).__name__}: {e}"
            )
            return source_key, None, int((time.monotonic() - t0) * 1000)

        if not (body or "").strip():
            return source_key, None, int((time.monotonic() - t0) * 1000)

        prompt = _build_prompt(framework, source_key, body)
        try:
            # 2026-05-27 — route through `dd-reduce-label` (non-reasoning
            # curated pool). Same rationale as chapter_assign: short
            # structured-output task that doesn't need <think> blocks.
            # 2-3× speedup on per-call latency expected.
            raw, _meta = await chat_judge_bandit_async(
                prompt,
                max_tokens=_MAX_TOKENS,
                temperature=_TEMPERATURE,
                response_format=_DISTILL_RESPONSE_FORMAT,
                dd_process="dd-reduce-label",
            )
        except Exception as e:
            logger.warning(
                f"[doc_distill] LLM failed for {source_key}: "
                f"{type(e).__name__}: {e}"
            )
            return source_key, None, int((time.monotonic() - t0) * 1000)

        parsed = _parse(raw)
        if not parsed:
            return source_key, None, int((time.monotonic() - t0) * 1000)

        distillate, err = _try_validate(parsed)
        # ONE repair attempt on Pydantic-fail.
        if distillate is None and _MAX_REPAIR_ATTEMPTS > 0:
            repair_prompt = (
                prompt
                + f"\n\nPRIOR OUTPUT was REJECTED: {err}\n"
                + f"Emit valid JSON exactly per the schema above."
            )
            try:
                # 2026-05-27 — same dd_process as the primary call so the
                # bandit's cooldown / reward state stays coherent.
                raw2, _ = await chat_judge_bandit_async(
                    repair_prompt,
                    max_tokens=_MAX_TOKENS,
                    temperature=0.0,
                    response_format=_DISTILL_RESPONSE_FORMAT,
                    dd_process="dd-reduce-label",
                )
                parsed2 = _parse(raw2)
                if parsed2:
                    distillate, _ = _try_validate(parsed2)
            except Exception:
                pass

        wall_ms = int((time.monotonic() - t0) * 1000)
        return source_key, distillate, wall_ms


# -------------------------------------------------------------- #
# Cache + persistence                                             #
# -------------------------------------------------------------- #
def _manifest_hash(*, slug: str, relevant_files: list[str]) -> str:
    h = sha256()
    h.update(_PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(relevant_files):
        h.update(b"|")
        h.update(k.encode())
    return h.hexdigest()[:16]


def _versioned_key(slug: str, manifest: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/doc_distill/{manifest}.json"


def _latest_key(slug: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/doc_distill-latest.json"


async def load_distillates(minio, slug: str) -> dict:
    """Reads the latest doc_distill blob. Used by chapter_propose and
    chapter_assign. Returns {} on miss."""
    try:
        text = await minio.read_text(_latest_key(slug))
        data = json.loads(text)
        return data.get("distillates") or {}
    except Exception:
        return {}


# -------------------------------------------------------------- #
# LangGraph node                                                  #
# -------------------------------------------------------------- #
@traced("doc_distill")
async def doc_distill(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []

    if not slug or not relevant_files:
        return {
            "doc_distill_ref": None,
            "doc_distill_stats": {
                "skipped": "no_files",
                "n_files": 0,
            },
        }

    n = len(relevant_files)
    t0 = time.monotonic()
    await emit_progress(
        thread_id, "doc_distill", "start",
        n_files=n, pass_through_threshold=_PASS_THROUGH_THRESHOLD,
    )

    # Pass-through for small N — downstream uses raw bodies directly.
    if n <= _PASS_THROUGH_THRESHOLD:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "doc_distill", "done",
            skipped="pass_through_small_n", n_files=n, wall_ms=wall_ms,
        )
        return {
            "doc_distill_ref": None,
            "doc_distill_stats": {
                "skipped": "pass_through_small_n",
                "n_files": n,
                "wall_ms": wall_ms,
            },
        }

    # Cache fast-path.
    minio = get_storage()
    manifest = _manifest_hash(slug=slug, relevant_files=relevant_files)
    vkey = _versioned_key(slug, manifest)
    lkey = _latest_key(slug)
    if await minio.exists(vkey) and await minio.exists(lkey):
        try:
            cached_text = await minio.read_text(vkey)
            cached = json.loads(cached_text)
            wall_ms = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_files": n,
                "n_distilled": len((cached or {}).get("distillates") or {}),
                "manifest_hash": manifest,
                "cache_hit": True,
                "wall_ms": wall_ms,
            }
            await emit_progress(
                thread_id, "doc_distill", "done",
                cache_hit=True, n_distilled=stats["n_distilled"],
                wall_ms=wall_ms,
            )
            return {"doc_distill_ref": lkey, "doc_distill_stats": stats}
        except Exception:
            pass

    # Fan out parallel distillation.
    sem = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _distill_one(sem, minio, slug, k) for k in relevant_files
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    distillates: dict[str, dict] = {}
    failures: list[str] = []
    for k, dist, _wall in results:
        if dist is not None:
            distillates[k] = dist.model_dump()
        else:
            failures.append(k)

    payload = {
        "prompt_version": _PROMPT_VERSION,
        "manifest_hash":  manifest,
        "framework_slug": slug,
        "distillates":    distillates,
        "n_files":        n,
        "n_distilled":    len(distillates),
        "n_failed":       len(failures),
        "failures":       failures[:20],  # cap for blob size
    }
    blob = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(vkey, blob, content_type="application/json")
    await minio.write(lkey, blob, content_type="application/json")

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_files": n,
        "n_distilled": len(distillates),
        "n_failed": len(failures),
        "manifest_hash": manifest,
        "cache_hit": False,
        "wall_ms": wall_ms,
    }
    await emit_progress(
        thread_id, "doc_distill", "done",
        cache_hit=False, n_distilled=len(distillates),
        n_failed=len(failures), wall_ms=wall_ms,
    )
    return {"doc_distill_ref": lkey, "doc_distill_stats": stats}
