from fastapi import APIRouter, HTTPException

from schemas.structures import (
    ModelConfig
)


router = APIRouter()

# =============================================================================
# Variables
# =============================================================================
model_config = ModelConfig(
    max_results = None, 
    search_type = None, 
    upload_date = None, 
    video_type = None, 
    duration = None, 
    features = None, 
    sort_by = None,
    video_url = None,
    channel_url = None,
    playlist_url = None
    )


# =============================================================================
# Endpoints
# =============================================================================
@router.get("/config", response_model = ModelConfig)
def get_model_config():
    return model_config

@router.put("/config", response_model = ModelConfig)
def update_agents_config(config: ModelConfig):
    model_config.max_results = config.max_results
    model_config.search_type = config.search_type
    model_config.upload_date = config.upload_date
    model_config.video_type = config.video_type
    model_config.duration = config.duration
    model_config.features = config.features
    model_config.sort_by = config.sort_by
    model_config.video_url = config.video_url
    model_config.channel_url = config.channel_url
    model_config.playlist_url = config.playlist_url
    return model_config