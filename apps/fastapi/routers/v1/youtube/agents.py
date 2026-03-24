from fastapi import (
    APIRouter, 
    HTTPException, 
    Request
)

from schemas.inputs import LLMConfig


router = APIRouter()


# =============================================================================
# Endpoints
# =============================================================================
@router.put("/config")
async def update_agents_config(config: LLMConfig, request: Request):
    redis_aio = request.app.state.redis_aio
    await redis_aio.json().set(
        "coelhonexus:youtube:agents:config", 
        "$", 
        config.model_dump(exclude_none = True)
    )
    return {
        "status": "saved", 
        "config": config.model_dump(
            exclude = {"api_key"})}

