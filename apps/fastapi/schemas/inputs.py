from pydantic import BaseModel, Field, ConfigDict
from typing import List

class LLMConfig(BaseModel):
    provider: str
    model: str | None
    temperature: float | None
    base_url: str | None
    api_key: str | None
    model_config = ConfigDict(extra = "allow")  # Accept extra fields


class YouTubeSearchConfig(BaseModel):
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

class ModelSpec(BaseModel):
    base_url: str | None
    api_key: str | None
    model: str | None
    temperature: float