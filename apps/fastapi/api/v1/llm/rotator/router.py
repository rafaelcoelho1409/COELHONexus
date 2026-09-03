"""Proxy to COELHO LLM Rotator for provider/model metadata."""
import os
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

ROTATOR_URL = os.getenv(
    "COELHO_LLM_ROTATOR_URL",
    "http://coelho-llm-rotator-fastapi.coelhonexus-dev.svc.cluster.local:8000/api/v1/llm/openai/v1",
)


@router.get("/providers")
async def rotator_providers():
    base = ROTATOR_URL.rsplit("/api/v1/llm/openai/v1", 1)[0]
    # rotator exposes /api/v1/llm/providers at same host
    url = base + "/api/v1/llm/providers"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return JSONResponse(content=r.json())
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e), "url": url})


@router.get("/models")
async def rotator_models():
    url = ROTATOR_URL + "/models"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return JSONResponse(content=r.json())
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e), "url": url})
