"""ycs/rag — PURE helpers shared by both the standard and adaptive graphs.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no async, no
clock. Lives at the `rag/` level because `standard/` and `adaptive/`
both call into it."""
from __future__ import annotations

import re
from typing import Any, TypeVar

from json_repair import loads as json_repair_loads
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel


_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>\s*")
_ModelT = TypeVar("_ModelT", bound = BaseModel)

# Cap on the prior turns we materialize into the prompt. Each turn = 2
# messages (Human + AI), so 8 turns = 16 messages. Big enough to keep
# multi-turn coherence; small enough that a 5-turn back-and-forth
# doesn't eat the LLM context budget.
_HISTORY_MESSAGES_CAP = 8


def history_to_messages(history: list[dict] | None) -> list[BaseMessage]:
    """Project `conversation_history` rows into the LangChain message
    shape `MessagesPlaceholder("history")` expects.

    Each row is `{"question": str, "answer": str, ...}` — the canonical
    Postgres shape `domains/ycs/conversation/service.py::get_history`
    returns. Empty `answer` rows are skipped (turns where the assistant
    crashed mid-stream and no row was persisted in the AI direction).

    Only the last `_HISTORY_MESSAGES_CAP` rows are kept; the older ones
    are dropped so the prompt budget stays predictable for long
    conversations. Older context is preserved indirectly via the
    `contextualize` node's question-rewrite (it sees all rows).

    Used by: generate / direct_answer / synthesize nodes."""
    if not history:
        return []
    rows = history[-_HISTORY_MESSAGES_CAP:]
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
    (direct_answer, contextualize, rewrite, generate, synthesize).

    Direct port of deprecated `graphs/youtube/helpers.py:L24-27` —
    extended for LangChain 1.x content blocks."""
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
    return _THINK_TAG_RE.sub("", text).strip()


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
