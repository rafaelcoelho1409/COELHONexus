"""ycs/rag — PURE helpers shared by both the standard and adaptive graphs.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no async, no
clock. Lives at the `rag/` level because `standard/` and `adaptive/`
both call into it."""
from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

from json_repair import loads as json_repair_loads
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from . import params, patterns


_ModelT = TypeVar("_ModelT", bound = BaseModel)


def history_to_messages(history: list[dict] | None) -> list[BaseMessage]:
    """Project `conversation_history` rows into the LangChain message
    shape `MessagesPlaceholder("history")` expects.

    Each row is `{"question": str, "answer": str, ...}` — the canonical
    Postgres shape `domains/ycs/conversation/service.py::get_history`
    returns. Empty `answer` rows are skipped (turns where the assistant
    crashed mid-stream and no row was persisted in the AI direction).

    Only the last `params.HISTORY_MESSAGES_CAP` rows are kept; the older ones
    are dropped so the prompt budget stays predictable for long
    conversations. Older context is preserved indirectly via the
    `contextualize` node's question-rewrite (it sees all rows).

    Used by: generate / direct_answer / synthesize nodes."""
    if not history:
        return []
    rows = history[-params.HISTORY_MESSAGES_CAP:]
    out: list[BaseMessage] = []
    for row in rows:
        q = (row.get("question") or "").strip()
        a = (row.get("answer")   or "").strip()
        if q:
            out.append(HumanMessage(content = q))
        if a:
            out.append(AIMessage(content = a))
    return out


def strip_think_tags(text: Any) -> str:
    """Strip `<think>...</think>` reasoning tokens from model output.

    Accepts either:
      - `str` — the classic shape from chat models.
      - `list` of content blocks — modern LangChain (1.x) returns
        `AIMessage.content` as `list[dict|str]` for thinking-aware
        models (Claude `thinking`, NIM reasoning, GPT-OSS, DeepSeek
        R1, Qwen 3 reasoning). Each block is either `{"type":"text",
        "text": "..."}`, `{"type":"thinking", "thinking": "..."}`,
        or a bare string. Reasoning blocks are DROPPED in line with
        what the `<think>` regex already does for inline tokens.
      - Anything else — coerced via `str()`.

    Why this matters (2026-06-11): the rotator's pool occasionally
    routes to a reasoning model whose response.content is a list,
    which the FAST-path direct_answer node was passing straight into
    `re.sub` → `TypeError: expected string or bytes-like object, got
    'list'`. Centralizing list-handling here covers every existing
    `strip_think_tags(response.content)` call site at once
    (direct_answer, contextualize, rewrite, generate, synthesize)."""
    if text is None:
        return ""
    if isinstance(text, list):
        parts: list[str] = []
        for block in text:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type")
                # Drop reasoning blocks outright — same intent as the
                # inline `<think>` regex below.
                if btype in ("thinking", "reasoning"):
                    continue
                # Standard text blocks (OpenAI/Anthropic shape).
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
        text = "".join(parts)
    elif not isinstance(text, str):
        text = str(text)
    text = patterns.THINK_TAG_RE.sub("", text)
    # 2026-09-17: some reasoning models (GPT-OSS/DeepSeek-R1/Qwen3
    # reasoning class, seen live via the rotator) emit a bare closing
    # `</think>` with NO matching opening tag — the serving harness
    # swallows the implicit opener but leaves the raw reasoning text
    # (often a verbatim draft of the final answer) in front of it.
    # `patterns.THINK_TAG_RE` can't match an unpaired tag, so it passed
    # through untouched, leaking duplicated reasoning + a literal
    # "</think>" into the shipped answer. Keep only what follows the
    # LAST such tag — that's the model's actual final answer.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1]
    return text.strip()


def parse_json_model_output(text: Any, model_cls: type[_ModelT]) -> _ModelT:
    """Parse an LLM JSON response into a validated Pydantic model.

    Uses `json_repair` so minor provider-side JSON defects (trailing
    commas, unescaped newlines, single quotes) don't fail the whole
    Ask flow. Raises on non-object payloads or schema mismatch."""
    cleaned = strip_think_tags(text)
    payload = json_repair_loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError(
            f"expected JSON object for {model_cls.__name__}, got "
            f"{type(payload).__name__}"
        )
    return model_cls.model_validate(payload)


def is_transient(exc: BaseException) -> bool:
    """True only for errors worth spending another attempt on. Used by
    `service.py`'s `resilient_ainvoke`/`hedged_ainvoke`."""
    if isinstance(exc, asyncio.TimeoutError):
        return True
    msg = str(exc).lower()
    if any(k in msg for k in params.NON_TRANSIENT_SUBSTRINGS):
        return False
    return any(k in msg for k in params.TRANSIENT_SUBSTRINGS)


def extract_result_items(raw: Any) -> list[dict]:
    """Normalize whatever `langchain-mcp-adapters` hands back into a
    flat list of result dicts. Live-verified shape (2026-09-16,
    against the real `https://search.parallel.ai/mcp` endpoint):
    `tool.ainvoke()` returns `list[{"type": "text", "text": "<JSON
    string>"}]` (standard MCP text-content-block wrapping); the JSON
    string itself is `{"search_id": ..., "results": [{"url", "title",
    "publish_date", "excerpts": [...]}]}`.

    Stays defensive beyond that one confirmed shape — a raw string, a
    bare dict, or a differently-shaped list all degrade to SOMETHING
    usable rather than raising; only a hard structural mismatch
    ultimately returns []."""
    if not raw:
        return []
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "text" in raw[0]:
        texts = [b.get("text", "") for b in raw if isinstance(b, dict)]
        raw = "\n".join(t for t in texts if t)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return [{"excerpts": [raw]}] if raw.strip() else []
    if isinstance(raw, dict):
        raw = raw.get("results") or raw.get("data") or [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [{"excerpts": [str(raw)]}]


def format_web_search_results(raw: Any) -> str:
    """Render up to `params.MAX_RESULTS` result items into a short text
    block for the fallback prompt's `{web_context}` slot."""
    items = extract_result_items(raw)
    if not items:
        return ""
    parts: list[str] = []
    for item in items[:params.MAX_RESULTS]:
        title = item.get("title") or item.get("name") or ""
        url = item.get("url") or item.get("link") or ""
        excerpts = item.get("excerpts")
        if isinstance(excerpts, list):
            excerpt = " ".join(str(e) for e in excerpts if e)
        else:
            excerpt = str(
                item.get("excerpt") or item.get("snippet")
                or item.get("content") or item.get("text") or excerpts or ""
            )
        excerpt = excerpt[:params.EXCERPT_CHAR_CAP]
        header = f"[{title}]({url})" if (title or url) else ""
        block = f"{header}\n{excerpt}".strip()
        if block:
            parts.append(block)
    return "\n\n---\n\n".join(parts)
