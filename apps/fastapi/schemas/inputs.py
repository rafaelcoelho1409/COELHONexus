from pydantic import BaseModel, ConfigDict
from typing import List, Literal

class LLMConfig(BaseModel):
    provider: str = "NVIDIA"
    model: str | None = None
    temperature: float | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_config = ConfigDict(extra = "allow")  # Accept extra fields

class YouTubeSearchConfig(BaseModel):
    query: str | None = None
    max_results: int | None = 10  # 0 = all videos (channel/playlist only, not allowed for /search)
    upload_date: Literal[
        "Last Hour",
        "Today",
        "This Week",
        "This Month",
        "This Year"
    ] | None = None
    video_type: Literal[
        "Video",
        "Channel",
        "Playlist",
        "Movie"
    ] | None = None
    duration: Literal[
        "Under 4 minutes",
        "4 - 20 minutes",
        "Over 20 minutes"
    ] | None = None
    features: list[Literal[
        "Live",
        "4K",
        "HD",
        "Subtitles/CC",
        "Creative Commons",
        "360",
        "VR180",
        "3D",
        "HDR",
        "Location",
        "Purchased"
    ]] | None = None
    sort_by: Literal[
        "Relevance",
        "Upload Date",
        "View count",
        "Rating"
    ] | None = None
    offset: int = 0  # Skip first N videos (for pagination)
    video_ids: list[str] | None = None
    channel_id: str | None = None
    playlist_id: str | None = None
    include_transcription: bool = True  # Fetch full transcription for each video
    transcription_languages: list[str] | None = None  # e.g. ["en", "pt"] - None = best available (English priority)


class TranscriptionRequest(BaseModel):
    video_ids: list[str]
    languages: list[str] | None = None  # e.g. ["pt", "en"] - if None, uses first available
