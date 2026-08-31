"""OpenAI-compatible proxy over the LLM rotator.

Exposes the rotator's LiteLLM Router + ParetoBandit as a standard
OpenAI `/v1` API so any OpenAI SDK (browser-use `ChatOpenAI`, etc.)
can point `base_url` here and get free-tier bandit routing for free.

Mounted at `/api/v1/llm/openai` → final paths:
  GET  /api/v1/llm/openai/v1/models
  POST /api/v1/llm/openai/v1/chat/completions

Proxied to the browser via FastHTML `proxy.py` → `https://coelhonexus.tail39dc94.ts.net/api/v1/llm/openai/v1/...`
and in-cluster at `http://coelhonexus-fastapi.coelhonexus.svc.cluster.local:8000/api/v1/llm/openai/v1/...`
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from domains.llm.rotator.chain import (
    build_llm_fallback_chain,
    ensure_dynamic_catalog,
)
from domains.llm.rotator.discovery import PROVIDERS, list_all_alive_models

logger = logging.getLogger(__name__)

router = APIRouter()


def _openai_to_lc_messages(openai_messages: list[dict]) -> list:
    """Convert OpenAI messages array to LangChain messages."""
    out = []
    for m in openai_messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        # OpenAI content may be string or list of blocks; keep as-is for vision
        if role == "system":
            out.append(SystemMessage(content=content or ""))
        elif role == "user":
            # content could be list[block]; LangChain handles it
            out.append(HumanMessage(content=content or ""))
        elif role == "assistant":
            tool_calls = m.get("tool_calls")
            lc_tool_calls = None
            if tool_calls:
                lc_tool_calls = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    lc_tool_calls.append({
                        "name": fn.get("name", ""),
                        "args": _safe_json_loads(fn.get("arguments", "{}")),
                        "id": tc.get("id", ""),
                        "type": "tool_call",
                    })
            out.append(AIMessage(
                content=content or "",
                tool_calls=lc_tool_calls or [],
                additional_kwargs={"tool_calls": tool_calls} if tool_calls else {},
            ))
        elif role == "tool":
            out.append(ToolMessage(
                content=str(content or ""),
                tool_call_id=m.get("tool_call_id", ""),
                name=m.get("name", ""),
            ))
        else:
            out.append(HumanMessage(content=str(content or "")))
    return out


def _safe_json_loads(s: str | dict) -> dict:
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {"_raw": s}


def _lc_tool_calls_to_openai(tool_calls: list[dict] | None) -> list[dict] | None:
    if not tool_calls:
        return None
    out = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        out.append({
            "id": tc.get("id", str(uuid.uuid4())),
            "type": "function",
            "function": {
                "name": tc.get("name", ""),
                "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
            },
        })
    return out or None


@router.get("/v1/models", response_model=None)
async def openai_list_models():
    """Return OpenAI-compatible model list from live discovery."""
    # Ensure catalog is primed for the UI picker
    try:
        await ensure_dynamic_catalog()
    except Exception:
        pass

    models = []
    # Virtual rotator entries — always first so ChatOpenAI can use `model="auto"`
    now = int(time.time())
    virtual = [
        {"id": "auto", "owned_by": "rotator"},
        {"id": "rotator", "owned_by": "rotator"},
        {"id": "coelhonexus-rotator", "owned_by": "rotator"},
    ]
    for v in virtual:
        models.append({
            "id": v["id"],
            "object": "model",
            "created": now,
            "owned_by": v["owned_by"],
        })

    # Live provider models
    try:
        by_provider = await list_all_alive_models()
        for provider, records in by_provider.items():
            for r in records:
                # r.model_id is bare id, prefix for litellm is handled server-side
                # Expose both bare and with provider prefix for convenience
                bare = r.model_id
                if not bare:
                    continue
                models.append({
                    "id": bare,
                    "object": "model",
                    "created": int(r.fetched_at) if r.fetched_at else now,
                    "owned_by": provider,
                })
    except Exception as e:
        logger.warning(f"[openai-compat] list_models discovery failed: {e}")

    # De-duplicate by id, keep first
    seen = set()
    uniq = []
    for m in models:
        if m["id"] not in seen:
            seen.add(m["id"])
            uniq.append(m)

    return JSONResponse(content={
        "object": "list",
        "data": uniq,
    })


@router.post("/v1/chat/completions", response_model=None)
async def openai_chat_completions(request: Request):
    """OpenAI-compatible chat completions over the rotator.

    Supports: model, messages, temperature, max_tokens/max_completion_tokens,
    stream, tools, tool_choice, response_format.
    """
    body: dict[str, Any] = await request.json()
    model = body.get("model") or "auto"
    messages_raw = body.get("messages") or []
    temperature = body.get("temperature")
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
    stream = bool(body.get("stream"))
    tools = body.get("tools")
    tool_choice = body.get("tool_choice")
    response_format = body.get("response_format")
    # Browser-use sends reasoning_effort etc — ignore gracefully

    if not messages_raw:
        return JSONResponse(status_code=400, content={"error": {"message": "messages is required", "type": "invalid_request_error"}})

    lc_messages = _openai_to_lc_messages(messages_raw)

    # Build rotator chain — always bandit-enabled fallback (FGTS-VA). The
    # `model` param is treated as a hint: "auto"/"rotator" → fallback, otherwise
    # we still use fallback (bandit picks best). Pinned-model support could be
    # added via build_pinned_chain_any(model) if needed.
    try:
        await ensure_dynamic_catalog()
    except Exception:
        pass

    llm = build_llm_fallback_chain()

    # Prepare invoke kwargs
    invoke_kwargs: dict[str, Any] = {}
    if temperature is not None:
        try:
            invoke_kwargs["temperature"] = float(temperature)
        except Exception:
            pass
    if max_tokens is not None:
        try:
            invoke_kwargs["max_tokens"] = int(max_tokens)
        except Exception:
            pass
    if tools is not None:
        invoke_kwargs["tools"] = tools
    if tool_choice is not None:
        invoke_kwargs["tool_choice"] = tool_choice
    if response_format is not None:
        invoke_kwargs["response_format"] = response_format

    # Bind tools if present — browser-use relies on this for structured tool calling
    if tools:
        try:
            llm = llm.bind_tools(tools)  # type: ignore[attr-defined]
            # tools already bound, remove from invoke_kwargs to avoid double-send
            invoke_kwargs.pop("tools", None)
            invoke_kwargs.pop("tool_choice", None)
        except Exception as e:
            logger.warning(f"[openai-compat] bind_tools failed: {e}")

    if stream:
        return await _streaming_response(llm, lc_messages, invoke_kwargs, model)
    else:
        return await _non_streaming_response(llm, lc_messages, invoke_kwargs, model)


MAX_RETRIES = 3

async def _non_streaming_response(llm, lc_messages, invoke_kwargs, requested_model: str):
    t0 = time.monotonic()
    last_e: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = await llm.ainvoke(lc_messages, **invoke_kwargs)
            break
        except Exception as e:
            last_e = e
            # Retry on provider payment/rate-limit/terms errors — rotator's Router may not auto-retry these
            msg = str(e).lower()
            retryable = any(k in msg for k in ("payment required", "quota", "rate limit", "429", "terms", "billing", "overloaded", "timeout", "500", "502", "503"))
            if attempt < MAX_RETRIES and retryable:
                logger.warning(f"[openai-compat] attempt {attempt}/{MAX_RETRIES} failed ({type(e).__name__}), retrying…")
                await asyncio.sleep(0.5 * attempt)
                continue
            logger.exception(f"[openai-compat] ainvoke failed after {attempt} attempt(s)")
            return JSONResponse(status_code=500, content={"error": {"message": f"{type(e).__name__}: {str(e)[:800]}", "type": "server_error", "attempt": attempt}})
    else:
        return JSONResponse(status_code=500, content={"error": {"message": f"{type(last_e).__name__}: {str(last_e)[:800]}" if last_e else "unknown", "type": "server_error"}})

    # result is AIMessage
    content = getattr(result, "content", "") or ""
    if isinstance(content, list):
        # Flatten list blocks to string for OpenAI response
        texts = []
        for b in content:
            if isinstance(b, dict):
                texts.append(b.get("text", "") or b.get("content", "") or "")
            elif isinstance(b, str):
                texts.append(b)
        content = "\n".join(t for t in texts if t)

    tool_calls = getattr(result, "tool_calls", None) or getattr(result, "additional_kwargs", {}).get("tool_calls")
    # Prefer structured tool_calls attribute
    lc_tool_calls = getattr(result, "tool_calls", None)
    openai_tool_calls = _lc_tool_calls_to_openai(lc_tool_calls) if lc_tool_calls else None
    # Fallback to additional_kwargs raw tool_calls
    if not openai_tool_calls and isinstance(tool_calls, list) and tool_calls and isinstance(tool_calls[0], dict) and "function" in tool_calls[0]:
        openai_tool_calls = tool_calls  # already OpenAI shape

    response_metadata = getattr(result, "response_metadata", {}) or {}
    model_used = response_metadata.get("model_name") or response_metadata.get("model") or requested_model
    finish_reason = response_metadata.get("finish_reason") or ("tool_calls" if openai_tool_calls else "stop")

    usage = getattr(result, "usage_metadata", None)
    prompt_tokens = completion_tokens = 0
    if usage:
        try:
            if isinstance(usage, dict):
                prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            else:
                prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        except Exception:
            pass

    resp = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_used,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content if not openai_tool_calls else (content or None),
                **({"tool_calls": openai_tool_calls} if openai_tool_calls else {}),
            },
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    return JSONResponse(content=resp)


async def _streaming_response(llm, lc_messages, invoke_kwargs, requested_model: str):
    """SSE streaming compatible with OpenAI's `stream: true`."""
    async def gen():
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        model_used = requested_model
        # First chunk: role
        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_used, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
        try:
            async for chunk in llm.astream(lc_messages, **invoke_kwargs):
                # chunk is AIMessageChunk
                c = getattr(chunk, "content", "") or ""
                if isinstance(c, list):
                    texts = []
                    for b in c:
                        if isinstance(b, dict):
                            texts.append(b.get("text", "") or "")
                        elif isinstance(b, str):
                            texts.append(b)
                    c = "".join(texts)
                if c:
                    payload = {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_used,
                        "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                # Tool call chunks — forward if present
                tc = getattr(chunk, "tool_calls", None) or getattr(chunk, "tool_call_chunks", None)
                if tc:
                    for item in tc if isinstance(tc, list) else [tc]:
                        if isinstance(item, dict) and item.get("name"):
                            payload = {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model_used,
                                "choices": [{"index": 0, "delta": {"tool_calls": [_lc_tool_calls_to_openai([item])[0]]}, "finish_reason": None}],
                            }
                            yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:
            logger.exception("[openai-compat] stream failed")
            err = json.dumps({"error": {"message": str(e)[:500], "type": "server_error"}})
            yield f"data: {err}\n\n"
        # Final chunk
        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_used, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
