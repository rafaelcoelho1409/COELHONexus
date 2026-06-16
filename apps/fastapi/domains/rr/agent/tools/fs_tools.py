"""LangChain @tool wrappers around the module-level fs helpers.

LLM subagents can ONLY interact with shared state via tool calls — they
can't `import` module dicts. These thin wrappers expose `fs_read` /
`fs_write` / `fs_list` from `state.py` as LangChain tools the
discovery / deep_read / synthesis / report subagents hold.

All tools take `scan_id` as their first argument so the LLM is forced
to thread it through every call (the orchestrator's prompt provides
it via the task description).

THE BIG FIX (2026-06-12 step-7): `stash_discovery_result` now uses
`InjectedState` — the tool reaches into the agent's message history,
finds the last ToolMessage (the MCP result), and stashes it. The LLM
no longer has to transcribe a 5KB JSON string into a tool arg. This
eliminates the "JSON truncated at char 4659" failure class.
"""
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
from ...runtime.fs_mirror import mirror_write_sync
from ...runtime.llm_counter import set_phase as _set_llm_phase


logger = logging.getLogger(__name__)


def _mirror(scan_id: str, path: str, value: Any) -> None:
    """Mirror a successful fs_write to Redis so FastAPI can introspect
    per-node state via `GET /scan/{id}/fs/{path}`. Best-effort — the
    underlying fs_write already happened; this is a parallel read-side
    affordance for the drawer."""
    try:
        mirror_write_sync(scan_id, path, value)
    except Exception as e:
        logger.warning(f"[fs-tool] mirror failed for {path}: {e}")


def _safe_emit(scan_id: str, phase: str, message: str) -> None:
    """Best-effort SSE emit. The fs-write tools call this so progress
    flows even while the orchestrator is blocked waiting on a subagent
    (PhaseEventsMiddleware only fires on orchestrator after_model, so
    in-subagent ticks would otherwise vanish). Wrapped in a try so a
    Redis hiccup never sinks the underlying write."""
    try:
        emit_event_sync(scan_id, phase, message=message)
    except Exception as e:
        logger.warning(f"[fs-tool] emit {phase!r} failed: {e}")


# --------------------------------------------------------------------------- #
# MCP result parser — handles the shapes langchain-mcp-adapters returns:
#   - list[dict]                  (already-parsed paper records)
#   - str (JSON-encoded list)
#   - list[TextContent]           ([{"type":"text","text":"<JSON>"}, ...])
#   - dict {"text": "<JSON>"}
# --------------------------------------------------------------------------- #
def _parse_tool_message_content(content: Any) -> list[dict]:
    """Same robust parser as tools/discovery.py — returns paper list[dict]
    from whatever shape a ToolMessage.content carries."""
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


# --------------------------------------------------------------------------- #
# Discovery → stash via InjectedState (no LLM transcription of 5KB JSON)
# --------------------------------------------------------------------------- #
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
    # Find the most-recent ToolMessage in history (the MCP tool's return).
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

    # 2026-06-16 idempotency guard. Scan 28094718 had the HN subagent
    # stash THREE times in a row with count=0 (LLM retried, prompt-level
    # "STASH ONCE" rule ignored), then a fourth time with count=5. We
    # accept upgrades (existing=0 → new=5 wins, existing=5 → new=12
    # wins) but refuse downgrades or no-op repeats. This eliminates the
    # wasted LLM turns and keeps the highest-quality stash on disk.
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
            # Both empty — accept the no-op but signal "stop retrying".
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
    # Phase contextvar for LLM-counter attribution (Path A 2026-06-16).
    # Any subsequent LLM call by this subagent (or by the orchestrator
    # processing the stash result) attributes to "discovery".
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


# --------------------------------------------------------------------------- #
# Deep_read → write per-paper extraction
# --------------------------------------------------------------------------- #
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
    fs_write(scan_id, path, payload)
    _mirror(scan_id, path, payload)
    try: _set_llm_phase("deep_read")
    except Exception: pass
    logger.info(
        f"[fs-tool] write_extraction scan_id={scan_id} arxiv_id={arxiv_id} "
        f"confidence={payload['confidence']:.2f} path={path}"
    )
    # Surface per-paper progress directly so the UI ticks 1/N → N/N even
    # while the orchestrator is blocked waiting for the deep_read subagent
    # to finish. `top_n.json` carries the denominator.
    n_done = len(fs_list(scan_id, prefix=FS_DIR_EXTRACTIONS + "/"))
    top_n  = fs_read(scan_id, FS_FILE_TRIAGE_TOPN) or []
    n_total = len(top_n) if isinstance(top_n, list) else n_done
    _safe_emit(scan_id, "deep_read",
               f"{n_done}/{n_total} extractions written")
    return f"wrote extraction for {arxiv_id} to {path}"


# --------------------------------------------------------------------------- #
# Synthesis → read extractions list + write synthesis report
# --------------------------------------------------------------------------- #
@tool
def list_extractions(scan_id: str) -> str:
    """List all extraction file paths for this scan. Returns a newline-
    separated list."""
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
    """Read the triage-ranked top-N paper list. Returns JSON string of
    NormalizedPaper dicts (arxiv_id, title, abstract, signal, ...).
    Used by synthesis + report subagents."""
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
            Defaults to empty dict (no per-paper assignment) — backwards-
            compatible with older callsites that didn't provide it. Without
            per_paper_themes the digest's per-item themes will be empty `[]`.

    2026-06-16 (post-f52fb84a): per_paper_themes added so synthesis owns
    the theme-to-paper assignment instead of the report subagent. Removes
    the redundant write_digest path that the LLM kept emitting `{` for.
    """
    cleaned_ppt: dict[str, list[str]] = {}
    if per_paper_themes:
        themes_set = {t for t in themes if isinstance(t, str)}
        for aid, t_list in per_paper_themes.items():
            if not isinstance(aid, str) or not isinstance(t_list, list):
                continue
            kept = [t for t in t_list if isinstance(t, str) and t in themes_set]
            # Enforce skill HARD RULE: max 2 themes per paper. Truncate
            # defensively; the prompt also says this, but truncate so a
            # rogue emission doesn't poison the digest.
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


# --------------------------------------------------------------------------- #
# Report → write final digest
# --------------------------------------------------------------------------- #
import re as _re

# Valid JSON escape chars per RFC 8259: `"`, `\`, `/`, `b`, `f`, `n`, `r`, `t`,
# `u`. Anything else after a single backslash is malformed — escape it.
_STRAY_BS_RE = _re.compile(r'\\(?!["\\/bfnrtu])')

# A `\u` that is NOT followed by exactly 4 hex digits is malformed (the
# observed failure: `\u` at end-of-string or with a non-hex byte). We
# double the backslash so json.loads treats it as a literal `\u` text
# instead of an escape opener.
_TRUNCATED_U_RE = _re.compile(r'\\u(?![0-9a-fA-F]{4})')

# Missing-comma between `}` and the next `{` (also `]"` patterns).
# Cheap regex pass — won't fix every missing-comma case but catches the
# common LLM omission between adjacent array elements.
_MISSING_COMMA_BRACE_RE  = _re.compile(r'(})(\s*)(\{)')
_MISSING_COMMA_BRACKET_RE = _re.compile(r'(])(\s*)(\[)')
_MISSING_COMMA_QUOTE_RE   = _re.compile(r'(")(\s*\n\s*)(")')

# Smart quotes / curly punctuation — common Word-paste artifacts that
# the LLM sometimes echoes into JSON strings.
_SMART_QUOTE_MAP = {
    '‘': "'", '’': "'",
    '“': '"', '”': '"',
    '–': '-', '—': '-',
}


def _repair_json_escapes(text: str) -> str:
    r"""Multi-pass JSON repair for common LLM emission bugs.

    Applies (in order):
      1. smart-quote → ascii         (Word-paste artifacts)
      2. truncated `\\u`             (the 2026-06-15 failure mode)
      3. stray single backslashes    (LaTeX math: `\\beta`, `\\hat{x}`)
      4. missing `,` between `}{`    (LLM array-elt omission)
      5. missing `,` between `][`
      6. missing `,` between `""` on separate lines

    Each repair is idempotent. None will turn a structurally-broken
    document into valid JSON, but together they cover ~95% of observed
    write_digest bounces. The remaining 5% fall through to the raw-text
    dump path in write_digest.
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
    """Try strict-parse → repaired-parse. Returns (payload, repair_tag).
    repair_tag is None on strict-parse success, else the name of the
    repair pass that worked. Raises json.JSONDecodeError if neither
    parses (caller writes the raw bytes path)."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    repaired = _repair_json_escapes(text)
    try:
        return json.loads(repaired), "repair"
    except json.JSONDecodeError:
        pass
    # Last-ditch: extract the largest balanced {...} substring + repair.
    # Helps when the LLM wrapped JSON in prose ("Here is the digest: {...}").
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


# Per-scan debug attempt counter so successive failures land in distinct
# `_debug/write_digest_attempt_<N>.txt` files instead of overwriting.
_DEBUG_ATTEMPT_COUNTERS: dict[str, int] = {}

# Per-scan failed-attempt counter for the retry-budget guard. Counts every
# write_digest call that returned ERROR (truncated input, parse failure,
# schema validation, etc.). Scan f52fb84a hit 6 failures in 8 minutes
# emitting literal `{` every time; the budget stops that pattern after
# `_MAX_FAILED_ATTEMPTS` strikes.
_FAILED_ATTEMPT_COUNTERS: dict[str, int] = {}

# 2026-06-16: write_digest hard caps.
#
# `_MIN_DIGEST_JSON_LEN` (50 chars): scan f52fb84a's report subagent
# emitted `digest_json="{"` six times. Any legitimate digest is thousands
# of chars (4 items × dense extraction fields ≈ 4-10 KB), so 50 chars is
# a generous floor that catches `{`, `}`, `{}`, `null`, prose snippets,
# tool-call truncations. Pre-parse — saves the multi-stage repair cost.
#
# `_MAX_FAILED_ATTEMPTS` (2 strikes): the report subagent's internal
# tool-call loop can retry write_digest several times before yielding.
# After 2 failed attempts the failure isn't a transient blip — the LLM
# is structurally confused. Surface "give up" so DeepAgents' subagent
# loop exits and falls back to respond_in_format / Python rebuild.
_MIN_DIGEST_JSON_LEN: int = 50
_MAX_FAILED_ATTEMPTS: int = 2


def _next_debug_attempt(scan_id: str) -> int:
    n = _DEBUG_ATTEMPT_COUNTERS.get(scan_id, 0) + 1
    _DEBUG_ATTEMPT_COUNTERS[scan_id] = n
    return n


def _bump_failed(scan_id: str) -> int:
    """Increment the failed-attempt counter for this scan. Returns the
    NEW count after the bump."""
    n = _FAILED_ATTEMPT_COUNTERS.get(scan_id, 0) + 1
    _FAILED_ATTEMPT_COUNTERS[scan_id] = n
    return n


def _peek_last_model() -> str | None:
    """Best-effort: return the LiteLLM Router's most recent deployment
    identity for failure logs. Tells us which rotator arm emitted the
    weak digest_json. None on import failure / missing attr (don't break
    write_digest just because telemetry is unavailable)."""
    try:
        import litellm
        last = getattr(litellm, "last_response", None)
        if last is None:
            return None
        model = getattr(last, "model", None)
        if model:
            return str(model)
        # Fallback: dict-shaped response
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

    BULLETPROOF (2026-06-15): never bounces the LLM. Strict parse →
    multi-pass repair → balanced-slice extraction → raw-bytes wrapper.
    Always succeeds and always emits a phase event. On any parse-style
    failure the raw bytes go to `_debug/write_digest_attempt_N.txt` for
    forensics and the parsed-as-best-we-can payload still lands in
    `digest.json`. The downstream Python `_build_digest_from_fs`
    rebuilds the canonical digest from triage/extractions/synthesis
    regardless, so a malformed LLM digest never blocks persistence.

    DIGEST-SCHEMA GATE (2026-06-15, post-77f47013): after a successful
    parse, the payload is validated against `DigestSchema` (the same
    schema the report subagent's `response_format` binds). If the LLM
    emitted structurally valid JSON but the fields don't satisfy
    DigestSchema (`items` non-empty, `themes` non-empty, summary length,
    etc.), we RETURN an actionable error string instead of storing the
    weak payload. The subagent's tool-call loop sees the error and
    retries — same effect as Pydantic rejecting respond_in_format. Two
    parallel paths (write_digest + respond_in_format) now share the
    same validation gate; the RAW fallback only triggers when BOTH the
    multi-pass repair AND the schema validation fail unrecoverably.

    SYNTHESIS-PRECONDITION GATE (2026-06-15, post-0103a78d): refuses
    the write outright when `fs/synthesis/report.json` doesn't exist.
    Scan 0103a78d showed the report subagent dispatched 3 min BEFORE
    synthesis (orchestrator phase-order violation), so per-item themes
    were emitted as `[]` (the LLM had no top-level theme set to draw
    from) and `_build_digest_from_fs` correctly merged in empties.
    Refusing the write forces the orchestrator to dispatch synthesis
    first; the LLM sees the actionable error and retries after the
    synthesis subagent returns. Cheap structural guard with no false
    positives — every legitimate write_digest call must come AFTER a
    synthesis write.
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

    # Retry-budget guard — refuse if we've already burned the budget
    # on this scan. Returns a hard-stop message so DeepAgents' subagent
    # tool loop yields (no more retries) and the Python rebuild path
    # produces the final digest from upstream fs artifacts.
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

    # Min-length guard — reject obviously-truncated input pre-parse.
    # Scan f52fb84a hit `digest_json="{"` 6 times; 50 chars is well
    # under any legitimate digest size (4-item digest is ~4-10 KB).
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
        # Final fallback: store the RAW bytes wrapped in a minimal
        # envelope so the subagent succeeds + future debugging has the
        # exact bytes that broke parsing.
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
        # Bump failure counter so the retry budget catches repeated
        # all-repairs-failed emissions.
        new_count = _bump_failed(scan_id)
        model_id = _peek_last_model() or "unknown"
        log_msg = (
            f"stored RAW after all repairs failed (debug={debug_path}); "
            f"model={model_id} "
            f"failed_attempts={new_count}/{_MAX_FAILED_ATTEMPTS}; "
            f"Python `_build_digest_from_fs` will rebuild from upstream fs"
        )
    # 2026-06-15 DigestSchema gate. When parse succeeded (not the RAW
    # envelope) AND the payload is a dict, validate it against the
    # DigestSchema Pydantic model. min_length constraints on `items` /
    # `themes` and the min summary length stop weak emissions BEFORE
    # they hit disk. The LLM gets back the Pydantic error message
    # verbatim and the tool-loop re-prompts. RAW-envelope payloads
    # (the unrecoverable JSON failure path) skip this gate since
    # they're structurally a `_raw` wrapper, not a digest.
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
            # Format a brief, actionable error so the LLM can retry
            # with a correct payload. We don't try to re-parse repair_tag
            # repairs — if the schema fails, the LLM made a content-shape
            # error (empty list, too-short summary, missing field), not a
            # JSON-encoding error.
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

    # 2026-06-15 anti-clobber guard. Scan 0b160aec showed the report
    # subagent call write_digest 3 times: first with items=4 (good),
    # then twice with items=0 (LLM emitted a valid-but-empty
    # respond_in_format payload after the first good emission). Each
    # empty write OVERWROTE the prior good digest, eventually leaving
    # fs/digest.json with no items — degraded=no_llm_per_item_themes
    # downstream. Refuse the overwrite when incoming is empty AND a
    # prior write left non-empty items on disk. Returns SUCCESS so the
    # subagent's internal loop doesn't retry, but skips the actual
    # mutation. The good digest stays put.
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
