"""LLM rotator health probe.

Single endpoint that fires one round-trip through the production rotator
(LiteLLM Router + ParetoBandit) so an operator can verify, in one curl,
that the whole stack — env keys, provider catalogs, cooldown state in
Redis, OTel exporters to Alloy + LangFuse — is wired correctly.

  GET /api/v1/llm/health
      → {ok, model_used, latency_ms, content_preview}

A failure path returns {ok: false, error_type, error_message} with a 503;
the operator can correlate that against the LangFuse trace (search by
trace_id returned in the response body when OTel is up).
"""
import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage

from services.llm.chain import build_llm_fallback_chain


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def llm_health() -> JSONResponse:
    chain = build_llm_fallback_chain()
    t0 = time.monotonic()
    try:
        resp = await chain.ainvoke([
            HumanMessage(content="Answer in exactly one word: ping"),
        ])
    except Exception as e:
        dt_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("[llm-health] rotator call failed")
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "latency_ms": dt_ms,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )

    dt_ms = int((time.monotonic() - t0) * 1000)
    md = getattr(resp, "response_metadata", {}) or {}
    content = getattr(resp, "content", "") or ""
    return JSONResponse(content={
        "ok": True,
        "latency_ms": dt_ms,
        "model_used": md.get("model_name") or md.get("model"),
        "content_preview": content[:120],
    })
