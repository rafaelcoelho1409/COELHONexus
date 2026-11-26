"""Book Distiller's isolated DeepAgents harness and structured invocation."""
from __future__ import annotations
import domains
from ... import agents, skills

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, ValidationError


logger = logging.getLogger(__name__)


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep Ask inputs valid JSON and reserve room for each evidence origin."""
    compacted = dict(payload)
    history = payload.get("conversation_history")
    if isinstance(history, list):
        compacted["conversation_history"] = [
            {
                "question": str(item.get("question", ""))[:900],
                "answer": str(item.get("answer", ""))[:700],
            }
            for item in history[-4:]
            if isinstance(item, dict)
        ]
    for key in ("question", "instruction", "retrieval_feedback", "external_search_status"):
        value = compacted.get(key)
        if isinstance(value, str):
            compacted[key] = value[:2000 if key == "question" else 500]
    for key in ("candidate_sub_questions", "sub_questions"):
        value = compacted.get(key)
        if isinstance(value, list):
            compacted[key] = [str(item)[:300] for item in value[:4]]

    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return compacted
    external = [item for item in evidence if isinstance(item, dict) and item.get("source") == "external_search"]
    book = [item for item in evidence if isinstance(item, dict) and item.get("source") != "external_search"]
    for maximum, text_chars in ((12, 1200), (10, 1000), (8, 850), (6, 700), (4, 500), (2, 300), (1, 150), (0, 0)):
        external_count = min(len(external), 3, maximum)
        selected = book[:maximum - external_count] + external[:external_count]
        compacted["evidence"] = [
            {
                "id": str(item.get("id", ""))[:200],
                "text": str(item.get("text", ""))[:text_chars],
                "source": str(item.get("source", ""))[:100],
                "citation": {
                    key: str(item.get("citation", {}).get(key, ""))[:limit]
                    for key, limit in (("title", 180), ("url", 500), ("snippet", 300), ("source", 100), ("provider_id", 100), ("published_at", 80))
                    if isinstance(item.get("citation"), dict) and item.get("citation", {}).get(key) is not None
                },
            }
            for item in selected
        ]
        encoded = json.dumps(compacted, ensure_ascii = False, default = str)
        if len(encoded) <= agents.ask.params.MAX_INPUT_CHARS:
            return compacted
    return compacted


def _parse_result[Schema: BaseModel](result: Any, schema: type[Schema]) -> Schema | None:
    if not isinstance(result, dict):
        return None
    structured = result.get("structured_response")
    if structured is not None:
        try:
            return schema.model_validate(structured)
        except ValidationError:
            pass
        if isinstance(structured, str):
            try:
                value = json.loads(structured)
                if isinstance(value, dict):
                    return schema.model_validate(value)
            except (json.JSONDecodeError, ValidationError):
                pass
    messages = result.get("messages")
    if not isinstance(messages, list):
        return None
    plain_text: list[str] = []
    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
            content = message.get("content")
            additional_kwargs = message.get("additional_kwargs")
            tool_calls = message.get("tool_calls") or (
                additional_kwargs.get("tool_calls")
                if isinstance(additional_kwargs, dict) else None
            )
        else:
            role = getattr(message, "type", None)
            content = getattr(message, "content", None)
            additional_kwargs = getattr(message, "additional_kwargs", None)
            tool_calls = getattr(message, "tool_calls", None) or (
                additional_kwargs.get("tool_calls")
                if isinstance(additional_kwargs, dict) else None
            )
        if role not in {"assistant", "ai", "AIMessage"}:
            continue
        if isinstance(content, list):
            content = "\n".join(
                part if isinstance(part, str) else part.get("text", "")
                for part in content
                if isinstance(part, str)
                or (isinstance(part, dict) and isinstance(part.get("text"), str))
            )
        if not isinstance(content, str):
            continue
        for offset, character in enumerate(content):
            if character != "{":
                continue
            try:
                value, _ = json.JSONDecoder().raw_decode(content[offset:])
                if isinstance(value, dict):
                    return schema.model_validate(value)
            except (json.JSONDecodeError, ValidationError):
                continue

        # Keep only final assistant text as a last-resort answer. Tool-call
        # messages and earlier reasoning are not user-facing answer content.
        candidate = content.strip()
        if (
            not tool_calls
            and candidate
            and not candidate.startswith("{")
            and not candidate.startswith("```json")
        ):
            plain_text.append(candidate)

    # Some OpenAI-compatible endpoints ignore structured-output mode and
    # return a good answer as plain text. Preserve that answer without
    # fabricating evidence citations; the UI can still display the response.
    for content in plain_text:
        try:
            answer = schema.model_validate({"answer": content, "evidence_ids": []})
            logger.warning(
                "BD Ask %s returned plain text; accepting it without evidence IDs",
                schema.__name__,
            )
            return answer
        except ValidationError:
            continue
    return None


async def invoke[Schema: BaseModel](
    agent: Any,
    schema: type[Schema],
    payload: dict[str, Any],
    *,
    skill_role: str,
) -> Schema:
    text = json.dumps(
        _compact_payload(payload), ensure_ascii = False, default = str,
    )
    files = skills.service.files_for(skill_role)
    last_error: Exception | None = None
    for attempt in range(agents.ask.params.MAX_RETRIES):
        try:
            user_text = text
            if attempt:
                user_text += (
                    "\n\nFormatting reminder: return a JSON object with exactly these fields: "
                    '"answer" (string) and "evidence_ids" (array of exact supplied IDs). '
                    "Do not use Markdown fences."
                )
            result = await asyncio.wait_for(
                agent.ainvoke({"messages": [{"role": "user", "content": user_text}], "files": files}),
                timeout = agents.ask.params.AGENT_TIMEOUT_S,
            )
            messages = result.get("messages") if isinstance(result, dict) else None
            if isinstance(messages, list):
                for message in messages:
                    if getattr(message, "usage_metadata", None):
                        await domains.settings.llm_counter.service.bump_response(message)
            parsed = _parse_result(result, schema)
            if parsed is not None:
                return parsed
            last_error = ValueError(f"BD Ask agent returned no valid {schema.__name__}")
        except Exception as error:  # noqa: BLE001 — retry is bounded and role-scoped.
            last_error = error
    raise ValueError(f"BD Ask agent invocation failed: {last_error}") from last_error
