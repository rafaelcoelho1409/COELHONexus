"""LangChain @tool wrappers around the module-level fs helpers in state.py."""
from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from langchain_core.tools import tool

try:
    from langgraph.prebuilt import InjectedState
except ImportError:                                                       # pragma: no cover
    from langgraph.prebuilt.tool_node import InjectedState                # type: ignore

from ..keys import (
    FS_DIR_DISCOVERY,
    FS_DIR_EXTRACTIONS,
    FS_FILE_DIGEST,
    FS_FILE_SYNTHESIS_REPORT,
    FS_FILE_TRIAGE_TOPN,
    fs_discovery_path,
    fs_extraction_path,
)
from .state import fs_list, fs_read, fs_write
from ...runtime.events import emit_event_sync
from ...runtime.extraction_cache import set_extraction_sync as _cache_extraction
from ...runtime.fs_mirror import mirror_write_sync
from ...runtime.llm_counter import (
    bump_retry_sync as _bump_retry,
    set_phase as _set_llm_phase,
)


logger = logging.getLogger(__name__)


def _mirror(scan_id: str, path: str, value: Any) -> None:
    """Mirror fs_write to Redis so FastAPI can read per-node state via GET /scan/{id}/fs/{path}."""
    try:
        mirror_write_sync(scan_id, path, value)
    except Exception as e:
        logger.warning(f"[fs-tool] mirror failed for {path}: {e}")


def _safe_emit(scan_id: str, phase: str, message: str) -> None:
    """Best-effort SSE emit inside a subagent (PhaseEventsMiddleware only fires on orchestrator after_model)."""
    try:
        emit_event_sync(scan_id, phase, message=message)
    except Exception as e:
        logger.warning(f"[fs-tool] emit {phase!r} failed: {e}")


def _parse_tool_message_content(content: Any) -> list[dict]:
    """Extract paper list[dict] from any ToolMessage.content shape langchain-mcp-adapters returns."""
    if content is None:
        return []
    if isinstance(content, list) and content and isinstance(content[0], dict) \
       and "type" not in content[0]:
        return [p for p in content if isinstance(p, dict)]
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    texts.append(t)
        combined = "".join(texts)
        if combined:
            try:
                data = json.loads(combined)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        return []
    if isinstance(content, str):
        try:
            data = json.loads(content)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    if isinstance(content, dict):
        if "text" in content:
            try:
                data = json.loads(content["text"])
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                return []
        if "papers" in content and isinstance(content["papers"], list):
            return content["papers"]
    return []


@tool
def stash_discovery_result(
    scan_id: str,
    source: str,
    state: Annotated[dict, InjectedState],
) -> str:
    """Stash the LAST MCP tool result from the agent's message history under
    `discovery/<source>.json` in this scan's virtual filesystem.

    DO NOT pass the result as an argument — call this AFTER calling your
    source's MCP tool (e.g. arxiv_search) and the tool result will be
    pulled from your conversation history automatically.

    Args:
        scan_id: Identifier for this radar scan (from your task description).
        source: One of 'arxiv', 'semantic_scholar', 'huggingface_daily_papers',
            'hn'. Must match the subagent's source.
    """
    messages = state.get("messages") or []
    last_tool_content = None
    for m in reversed(messages):
        if type(m).__name__ == "ToolMessage":
            last_tool_content = getattr(m, "content", None)
            break
    if last_tool_content is None:
        msg = "ERROR: no ToolMessage found in state — call your MCP tool BEFORE calling stash_discovery_result"
        logger.warning(f"[fs-tool] stash_discovery_result scan_id={scan_id} source={source!r}: {msg}")
        return msg

    papers = _parse_tool_message_content(last_tool_content)
    path = fs_discovery_path(source)
    n_new = len(papers)

    # Idempotency guard: accept upgrades (0→5, 5→12) but refuse downgrades or no-op repeats.
    existing = fs_read(scan_id, path)
    if isinstance(existing, list):
        n_existing = len(existing)
        if n_new <= n_existing and n_existing > 0:
            logger.info(
                f"[fs-tool] stash_discovery_result scan_id={scan_id} "
                f"source={source!r} REFUSED downgrade: incoming count={n_new} "
                f"<= existing count={n_existing}. Keeping existing."
            )
            return (
                f"already wrote {n_existing} {source} papers to {path}; "
                f"refusing incoming count={n_new} (no improvement). "
                f"Stop calling your source MCP tool — move on to your next "
                f"phase (don't search again)."
            )
        if n_new == 0 and n_existing == 0:
            logger.info(
                f"[fs-tool] stash_discovery_result scan_id={scan_id} "
                f"source={source!r} no-op stash (existing=0, new=0). "
                f"Subagent should stop and let triage proceed."
            )
            return (
                f"wrote 0 {source} papers to {path} (no-op — existing was "
                f"already 0). DO NOT retry; an empty stash is a legitimate "
                f"empty-result signal. Your discovery turn is complete."
            )

    fs_write(scan_id, path, papers)
    _mirror(scan_id, path, papers)
    try: _set_llm_phase("discovery")
    except Exception: pass
    logger.info(
        f"[fs-tool] stash_discovery_result scan_id={scan_id} "
        f"source={source!r} count={n_new} path={path} "
        f"(via InjectedState)"
    )
    if not papers:
        return (
            f"wrote 0 {source} papers to {path} — the MCP tool returned "
            f"an empty/unparseable result. This is a legitimate empty-result "
            f"signal; do NOT retry. Your discovery turn is complete."
        )
    return f"wrote {n_new} {source} papers to {path}"


@tool
def write_extraction(
    scan_id: str,
    arxiv_id: str,
    problem: str,
    method: str,
    math: str,
    how_to_build: str,
    money_angle: str,
    confidence: float = 0.5,
) -> str:
    """Persist a deep_read extraction for one paper.

    Args:
        scan_id: This scan's identifier (from your task description).
        arxiv_id: Canonical arxiv id (no version suffix), e.g. '2406.12345'.
        problem: 2-3 sentences — what real-world gap does the paper close.
        method: 4-6 sentences — how the paper does it.
        math: Key formulas (LaTeX) + their role in the method.
        how_to_build: Implementation notes — what to wire to what.
        money_angle: Commercial / portfolio applicability.
        confidence: Self-rated extraction confidence in [0, 1].
    """
    payload = {
        "arxiv_id":     arxiv_id,
        "problem":      problem,
        "method":       method,
        "math":         math,
        "how_to_build": how_to_build,
        "money_angle":  money_angle,
        "confidence":   max(0.0, min(1.0, float(confidence))),
    }
    path = fs_extraction_path(arxiv_id)
    # writing an arxiv_id not in top_n (orchestrator-hallucinated case).
    top_n_before = fs_read(scan_id, FS_FILE_TRIAGE_TOPN) or []
    top_n_ids    = (
        {p.get("arxiv_id") for p in top_n_before if isinstance(p, dict)}
        if isinstance(top_n_before, list) else set()
    )
    existing     = fs_read(scan_id, path) is not None
    is_off_topn  = bool(top_n_ids) and (arxiv_id not in top_n_ids)
    is_retry     = existing or is_off_topn
    fs_write(scan_id, path, payload)
    _mirror(scan_id, path, payload)
    try: _set_llm_phase("deep_read")
    except Exception: pass
    if is_retry:
        try:
            _bump_retry(scan_id, "deep_read")
        except Exception as e:
            logger.warning(f"[fs-tool] retry bump failed for {arxiv_id}: {e}")
        try:
            emit_event_sync(
                scan_id, "retry",
                message = (
                    f"deep_read re-entered for {arxiv_id} "
                    f"({'overwrite' if existing else 'off-top_n hallucination'})"
                ),
                summary = {
                    "phase":  "deep_read",
                    "kind":   "overwrite" if existing else "off_top_n",
                    "source": "synthesis",
                    "target": "deep_read",
                },
            )
        except Exception as e:
            logger.warning(f"[fs-tool] retry emit failed: {e}")
    try:
        _cache_extraction(arxiv_id, payload)
    except Exception as e:
        logger.warning(f"[fs-tool] extraction cache set failed for {arxiv_id}: {e}")
    logger.info(
        f"[fs-tool] write_extraction scan_id={scan_id} arxiv_id={arxiv_id} "
        f"confidence={payload['confidence']:.2f} path={path}"
        + (f" RETRY({'overwrite' if existing else 'off_top_n'})" if is_retry else "")
    )
    paths   = fs_list(scan_id, prefix=FS_DIR_EXTRACTIONS + "/")
    n_done  = len(paths)
    n_total = len(top_n_before) if isinstance(top_n_before, list) else n_done
    n_done_display = min(n_done, n_total) if n_total else n_done
    _safe_emit(scan_id, "deep_read",
               f"{n_done_display}/{n_total} extractions written")
    return f"wrote extraction for {arxiv_id} to {path}"


@tool
def list_extractions(scan_id: str) -> str:
    """List all extraction file paths for this scan. Returns a newline-separated list."""
    paths = fs_list(scan_id, prefix=FS_DIR_EXTRACTIONS + "/")
    return "\n".join(paths) if paths else "(no extractions yet)"


@tool
def read_extraction(scan_id: str, arxiv_id: str) -> str:
    """Read a single paper's extraction. Returns JSON string."""
    payload = fs_read(scan_id, fs_extraction_path(arxiv_id))
    if payload is None:
        return f"ERROR: no extraction for {arxiv_id}"
    return json.dumps(payload, default=str)


@tool
def read_top_n_papers(scan_id: str) -> str:
    """Read the triage-ranked top-N paper list. Returns JSON string of NormalizedPaper dicts."""
    payload = fs_read(scan_id, FS_FILE_TRIAGE_TOPN)
    if payload is None:
        return "ERROR: triage hasn't run yet — no top_n.json"
    return json.dumps(payload, default=str)


@tool
def write_synthesis_report(
    scan_id: str,
    themes: list[str],
    cross_paper_convergence: str,
    summary: str,
    per_paper_themes: dict[str, list[str]] | None = None,
) -> str:
    """Persist the synthesis report.

    Args:
        scan_id: This scan's identifier.
        themes: Short names of emerging themes spanning multiple papers
            (e.g. 'constrained decoding', 'speculative tool validation').
        cross_paper_convergence: 4-8 sentences describing where papers
            independently arrived at related ideas.
        summary: 2-3 sentence executive summary of what's notable this scan.
        per_paper_themes: Mapping `arxiv_id → [theme_name, ...]` assigning
            each top_n paper to which subset of the synthesis themes applies.
            HARD RULES:
              - Each entry's theme list must be a STRICT SUBSET of `themes`.
              - Max 2 themes per paper. 0 is OK (paper doesn't fit any theme).
              - NEVER copy the full top-level `themes` list into one paper.
              - Match by paper content (the deep_read extraction's `problem` /
                `method` fields), not by title token overlap.
            Defaults to empty dict — without per_paper_themes the digest's per-item themes will be empty `[]`.
    """
    cleaned_ppt: dict[str, list[str]] = {}
    if per_paper_themes:
        themes_set = {t for t in themes if isinstance(t, str)}
        for aid, t_list in per_paper_themes.items():
            if not isinstance(aid, str) or not isinstance(t_list, list):
                continue
            kept = [t for t in t_list if isinstance(t, str) and t in themes_set]
            # Max 2 themes per paper — truncate defensively even though the prompt also states this.
            cleaned_ppt[aid] = kept[:2]

    payload = {
        "themes":                  list(themes),
        "cross_paper_convergence": cross_paper_convergence,
        "summary":                 summary,
        "per_paper_themes":        cleaned_ppt,
    }
    fs_write(scan_id, FS_FILE_SYNTHESIS_REPORT, payload)
    _mirror(scan_id, FS_FILE_SYNTHESIS_REPORT, payload)
    try: _set_llm_phase("synthesis")
    except Exception: pass
    logger.info(
        f"[fs-tool] write_synthesis_report scan_id={scan_id} "
        f"themes={len(themes)} per_paper_themes={len(cleaned_ppt)} "
        f"path={FS_FILE_SYNTHESIS_REPORT}"
    )
    _safe_emit(scan_id, "synthesis", f"{len(themes)} themes clustered")
    return (
        f"wrote synthesis report to {FS_FILE_SYNTHESIS_REPORT} "
        f"({len(themes)} themes, {len(cleaned_ppt)} per-paper assignments)"
    )


@tool
def read_synthesis_report(scan_id: str) -> str:
    """Read the synthesis report. Used by the report subagent."""
    payload = fs_read(scan_id, FS_FILE_SYNTHESIS_REPORT)
    if payload is None:
        return "ERROR: synthesis hasn't written a report yet"
    return json.dumps(payload, default=str)


import re as _re

# Valid JSON escape chars per RFC 8259. Anything else after a single backslash is malformed.
_STRAY_BS_RE = _re.compile(r'\\(?!["\\/bfnrtu])')

# A `\u` not followed by exactly 4 hex digits is malformed — double the backslash.
_TRUNCATED_U_RE = _re.compile(r'\\u(?![0-9a-fA-F]{4})')

_MISSING_COMMA_BRACE_RE  = _re.compile(r'(})(\s*)(\{)')
_MISSING_COMMA_BRACKET_RE = _re.compile(r'(])(\s*)(\[)')
_MISSING_COMMA_QUOTE_RE   = _re.compile(r'(")(\s*\n\s*)(")')

# Smart quotes — common LLM emission artifacts that break JSON parsing.
_SMART_QUOTE_MAP = {
    '‘': "'", '’': "'",
    '“': '"', '”': '"',
    '–': '-', '—': '-',
}


def _repair_json_escapes(text: str) -> str:
    r"""Multi-pass JSON repair for common LLM emission bugs.

    Passes (in order): smart-quote → ascii, truncated \u, stray backslashes,
    missing comma between }{, between ][, between "" on separate lines.
    """
    out = text
    for bad, good in _SMART_QUOTE_MAP.items():
        out = out.replace(bad, good)
    out = _TRUNCATED_U_RE.sub(r'\\\\u', out)
    out = _STRAY_BS_RE.sub(r'\\\\', out)
    out = _MISSING_COMMA_BRACE_RE.sub(r'\1,\2\3', out)
    out = _MISSING_COMMA_BRACKET_RE.sub(r'\1,\2\3', out)
    out = _MISSING_COMMA_QUOTE_RE.sub(r'\1,\2\3', out)
    return out


def _try_parse_with_repairs(text: str) -> tuple[Any, str | None]:
    """Strict-parse → repaired-parse → balanced-slice extraction. Raises JSONDecodeError if all fail."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    repaired = _repair_json_escapes(text)
    try:
        return json.loads(repaired), "repair"
    except json.JSONDecodeError:
        pass
    first_brace = text.find('{')
    last_brace  = text.rfind('}')
    if first_brace >= 0 and last_brace > first_brace:
        try:
            return (
                json.loads(_repair_json_escapes(text[first_brace:last_brace + 1])),
                "slice_repair",
            )
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("all repair strategies failed", text, 0)


_DEBUG_ATTEMPT_COUNTERS: dict[str, int] = {}
_FAILED_ATTEMPT_COUNTERS: dict[str, int] = {}

# 50 chars: any legitimate digest is thousands of chars; catches `{`, `}`, `null`, prose snippets.
_MIN_DIGEST_JSON_LEN: int = 50
# 2 strikes: if both fail the LLM is structurally confused; surface "give up" so the tool loop exits.
_MAX_FAILED_ATTEMPTS: int = 2


def _next_debug_attempt(scan_id: str) -> int:
    n = _DEBUG_ATTEMPT_COUNTERS.get(scan_id, 0) + 1
    _DEBUG_ATTEMPT_COUNTERS[scan_id] = n
    return n


def _bump_failed(scan_id: str) -> int:
    n = _FAILED_ATTEMPT_COUNTERS.get(scan_id, 0) + 1
    _FAILED_ATTEMPT_COUNTERS[scan_id] = n
    return n


def _peek_last_model() -> str | None:
    """Best-effort: last LiteLLM deployment identity for failure logs."""
    try:
        import litellm
        last = getattr(litellm, "last_response", None)
        if last is None:
            return None
        model = getattr(last, "model", None)
        if model:
            return str(model)
        if isinstance(last, dict):
            return str(last.get("model") or "") or None
    except Exception:
        pass
    return None


@tool
def write_digest(scan_id: str, digest_json: str) -> str:
    """Persist the final ranked digest as JSON.

    Args:
        scan_id: This scan's identifier.
        digest_json: A JSON string with the assembled digest payload —
            top-level fields {scan_id, themes, summary, items: [...]}
            where each item has {arxiv_id, rank, signal, title, authors,
            summary, is_new, extraction, sources, themes}.

    Bulletproof: never bounces the LLM. Strict parse → multi-pass repair
    → balanced-slice extraction → raw-bytes wrapper. DigestSchema validation
    gate rejects weak emissions. Synthesis precondition gate refuses writes
    before synthesis/report.json exists.
    """
    # Synthesis precondition — refuse if synthesis hasn't run yet.
    if fs_read(scan_id, FS_FILE_SYNTHESIS_REPORT) is None:
        msg = (
            f"ERROR: cannot write_digest before synthesis runs. "
            f"`{FS_FILE_SYNTHESIS_REPORT}` does not exist yet. "
            f"Dispatch task(subagent_type='synthesis', "
            f"description='scan_id={scan_id}') FIRST, wait for it to "
            f"return, THEN call write_digest. The digest's per-item "
            f"`themes` field must reference the synthesis top-level "
            f"themes (a strict subset, max 2) — without synthesis, the "
            f"digest is structurally incomplete."
        )
        logger.warning(
            f"[fs-tool] write_digest scan_id={scan_id} REJECTED: "
            f"synthesis not yet on disk (phase-order violation)"
        )
        return msg

    n_failed_already = _FAILED_ATTEMPT_COUNTERS.get(scan_id, 0)
    if n_failed_already >= _MAX_FAILED_ATTEMPTS:
        msg = (
            f"ERROR: write_digest budget exhausted for this scan "
            f"({n_failed_already}/{_MAX_FAILED_ATTEMPTS} failed attempts). "
            f"Stop retrying. The Python rebuild path will assemble the "
            f"digest from triage + extractions + synthesis on disk — "
            f"your job is DONE. Emit respond_in_format(DigestSchema) "
            f"NOW to terminate the subagent."
        )
        logger.warning(
            f"[fs-tool] write_digest scan_id={scan_id} REJECTED: "
            f"retry budget exhausted ({n_failed_already}/{_MAX_FAILED_ATTEMPTS})"
        )
        return msg

    trimmed = (digest_json or "").strip()
    if len(trimmed) < _MIN_DIGEST_JSON_LEN:
        model_id = _peek_last_model() or "unknown"
        msg = (
            f"ERROR: digest_json was {len(trimmed)} chars — too short "
            f"to be a valid digest (min {_MIN_DIGEST_JSON_LEN}). The "
            f"full digest with all top_n items must be emitted in this "
            f"single argument. Do NOT emit a placeholder like `{{`, "
            f"`{{}}`, or `null`; the digest_json argument carries the "
            f"ENTIRE serialized payload as a JSON string."
        )
        new_count = _bump_failed(scan_id)
        logger.warning(
            f"[fs-tool] write_digest scan_id={scan_id} REJECTED: "
            f"truncated digest_json len={len(trimmed)} "
            f"(payload={trimmed[:40]!r}) "
            f"model={model_id} "
            f"failed_attempts={new_count}/{_MAX_FAILED_ATTEMPTS}"
        )
        return msg

    try:
        payload, repair_tag = _try_parse_with_repairs(digest_json)
        if repair_tag is None:
            log_msg = "parsed strict"
        else:
            log_msg = f"parsed after {repair_tag}"
    except json.JSONDecodeError as e:
        attempt_n = _next_debug_attempt(scan_id)
        debug_path = f"_debug/write_digest_attempt_{attempt_n}.txt"
        try:
            fs_write(scan_id, debug_path, digest_json)
            _mirror(scan_id, debug_path, digest_json)
        except Exception as dump_err:
            logger.warning(
                f"[fs-tool] write_digest scan_id={scan_id} debug-dump failed: {dump_err}"
            )
        payload = {
            "_raw":          digest_json,
            "_parse_error":  f"{type(e).__name__}: {e}",
            "_debug_path":   debug_path,
            "scan_id":       scan_id,
            "items":         [],
        }
        new_count = _bump_failed(scan_id)
        model_id = _peek_last_model() or "unknown"
        log_msg = (
            f"stored RAW after all repairs failed (debug={debug_path}); "
            f"model={model_id} "
            f"failed_attempts={new_count}/{_MAX_FAILED_ATTEMPTS}; "
            f"Python `_build_digest_from_fs` will rebuild from upstream fs"
        )

    is_raw_envelope = (
        isinstance(payload, dict)
        and "_raw" in payload
        and "_parse_error" in payload
    )
    if isinstance(payload, dict) and not is_raw_envelope:
        try:
            from ..schemas import DigestSchema
            DigestSchema.model_validate(payload)
        except Exception as ve:
            msg = (
                f"ERROR: DigestSchema validation failed: {ve}. "
                f"Fix the digest payload and call write_digest again. "
                f"Remember: items MUST be non-empty (one entry per top_n "
                f"paper), themes MUST be non-empty (copy from "
                f"fs/synthesis/report.json), summary MUST be >=50 chars."
            )
            new_count = _bump_failed(scan_id)
            model_id = _peek_last_model() or "unknown"
            logger.warning(
                f"[fs-tool] write_digest scan_id={scan_id} REJECTED: "
                f"{type(ve).__name__}: {str(ve)[:200]} "
                f"model={model_id} "
                f"failed_attempts={new_count}/{_MAX_FAILED_ATTEMPTS}"
            )
            return msg

    n_items = (
        len(payload.get("items", []) or []) if isinstance(payload, dict) else 0
    )

    # Anti-clobber: refuse empty overwrite when a prior good emission already exists on disk.
    existing = fs_read(scan_id, FS_FILE_DIGEST)
    if n_items == 0 and isinstance(existing, dict):
        existing_items = existing.get("items") or []
        if isinstance(existing_items, list) and len(existing_items) > 0:
            logger.warning(
                f"[fs-tool] write_digest scan_id={scan_id} REFUSED: "
                f"incoming items=0 would overwrite existing items={len(existing_items)}. "
                f"Keeping existing digest; the LLM's prior good emission stays."
            )
            return (
                f"already wrote {FS_FILE_DIGEST} with "
                f"{len(existing_items)} items; refusing empty overwrite"
            )

    fs_write(scan_id, FS_FILE_DIGEST, payload)
    _mirror(scan_id, FS_FILE_DIGEST, payload)
    logger.info(
        f"[fs-tool] write_digest scan_id={scan_id} items={n_items} "
        f"path={FS_FILE_DIGEST} ({log_msg})"
    )
    _safe_emit(scan_id, "report", f"{n_items} items in digest")
    return f"wrote final digest to {FS_FILE_DIGEST} ({log_msg})"
