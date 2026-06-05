"""One round-trip through the production rotator. Verifies env keys,
provider catalogs, Redis cooldown state, OTel exporters."""
import logging
import time
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage

from domains.llm.rotator.chain import (
    build_llm_fallback_chain,
    ensure_dynamic_catalog,
)


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def llm_health() -> JSONResponse:
    await ensure_dynamic_catalog()
    chain = build_llm_fallback_chain()
    t0 = time.monotonic()
    try:
        resp = await chain.ainvoke([
            HumanMessage(content = "Answer in exactly one word: ping"),
        ])
    except Exception as e:
        dt_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("[llm-health] rotator call failed")
        return JSONResponse(
            status_code = 503,
            content = {
                "ok": False,
                "latency_ms": dt_ms,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
    dt_ms = int((time.monotonic() - t0) * 1000)
    md = getattr(resp, "response_metadata", {}) or {}
    content = getattr(resp, "content", "") or ""
    return JSONResponse(
        content = {
            "ok": True,
            "latency_ms": dt_ms,
            "model_used": md.get("model_name") or md.get("model"),
            "content_preview": content[:120],
    })
