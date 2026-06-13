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


logger = logging.getLogger(__name__)


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
    fs_write(scan_id, path, papers)
    logger.info(
        f"[fs-tool] stash_discovery_result scan_id={scan_id} "
        f"source={source!r} count={len(papers)} path={path} "
        f"(via InjectedState)"
    )
    if not papers:
        return (
            f"wrote 0 {source} papers to {path} — the MCP tool returned "
            f"an empty/unparseable result. Check the previous ToolMessage."
        )
    return f"wrote {len(papers)} {source} papers to {path}"


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
    logger.info(
        f"[fs-tool] write_extraction scan_id={scan_id} arxiv_id={arxiv_id} "
        f"confidence={payload['confidence']:.2f} path={path}"
    )
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
) -> str:
    """Persist the synthesis report.

    Args:
        scan_id: This scan's identifier.
        themes: Short names of emerging themes spanning multiple papers
            (e.g. 'constrained decoding', 'speculative tool validation').
        cross_paper_convergence: 4-8 sentences describing where papers
            independently arrived at related ideas.
        summary: 2-3 sentence executive summary of what's notable this scan.
    """
    payload = {
        "themes":                  list(themes),
        "cross_paper_convergence": cross_paper_convergence,
        "summary":                 summary,
    }
    fs_write(scan_id, FS_FILE_SYNTHESIS_REPORT, payload)
    logger.info(
        f"[fs-tool] write_synthesis_report scan_id={scan_id} "
        f"themes={len(themes)} path={FS_FILE_SYNTHESIS_REPORT}"
    )
    return f"wrote synthesis report to {FS_FILE_SYNTHESIS_REPORT}"


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
@tool
def write_digest(scan_id: str, digest_json: str) -> str:
    """Persist the final ranked digest as JSON.

    Args:
        scan_id: This scan's identifier.
        digest_json: A JSON string with the assembled digest payload —
            top-level fields {scan_id, themes, summary, items: [...]}
            where each item has {arxiv_id, rank, signal, title, authors,
            summary, is_new, extraction, sources, themes}.
    """
    try:
        payload = json.loads(digest_json)
    except json.JSONDecodeError as e:
        msg = f"ERROR: invalid JSON: {e}"
        logger.warning(f"[fs-tool] write_digest scan_id={scan_id}: {msg}")
        return msg
    fs_write(scan_id, FS_FILE_DIGEST, payload)
    n_items = (
        len(payload.get("items", []) or []) if isinstance(payload, dict) else 0
    )
    logger.info(
        f"[fs-tool] write_digest scan_id={scan_id} items={n_items} "
        f"path={FS_FILE_DIGEST}"
    )
    return f"wrote final digest to {FS_FILE_DIGEST}"
