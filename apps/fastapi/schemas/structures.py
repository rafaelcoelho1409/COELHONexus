from pydantic import BaseModel, Field
from typing import List

class Entities(BaseModel):
    """Identifying information about entities."""
    names: List[str] = Field(
        ...,
        description = "All the person, organization, or business entities that "
        "appear in the text",
    )

class AgentsConfig(BaseModel):
    framework: str | None
    temperature_filter: float | None
    model_name: str | None
    api_key: dict | None

class ModelConfig(BaseModel):
    max_results: int | None
    search_type: str | None
    upload_date: str | None
    video_type: str | None
    duration: str | None
    features: list | None
    sort_by: str | None
    video_url: str | None
    channel_url: str | None
    playlist_url: str | None