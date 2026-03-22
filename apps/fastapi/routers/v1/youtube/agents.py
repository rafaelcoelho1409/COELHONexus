from fastapi import APIRouter, HTTPException
from langchain_openai import ChatOpenAI

from schemas.structures import AgentsConfig


router = APIRouter()

# =============================================================================
# Variables
# =============================================================================
agents_config = AgentsConfig(
    framework = None,
    temperature_filter = None,
    model_name = None,
    api_key = {"api_key": None})

# =============================================================================
# Endpoints
# =============================================================================
@router.get("/config", response_model = AgentsConfig, response_model_exclude = {"api_key"})
def get_agents_config():
    return agents_config

@router.put("/config", response_model = AgentsConfig, response_model_exclude = {"api_key"})
def update_agents_config(config: AgentsConfig):
    agents_config.framework = config.framework
    agents_config.temperature_filter = config.temperature_filter
    agents_config.model_name = config.model_name
    agents_config.api_key = config.api_key
    for key, value in agents_config.api_key["api_key"].items():
        os.environ[key] = value
    return agents_config