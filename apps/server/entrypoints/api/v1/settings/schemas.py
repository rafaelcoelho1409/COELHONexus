"""settings router — Pydantic request/response models (HTTP contract)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EndpointBody(BaseModel):
    """LLM endpoint config. `api_key=None` leaves the stored key unchanged;
    `api_key=""` clears it."""
    url: str = Field(min_length=1)
    model: str | None = None  # empty or missing: the server's default model (kept out of the public spec)
    api_key: str | None = None


class EmbeddingBody(BaseModel):
    """Embedding endpoint config — independent connection from chat's
    EndpointBody above, same shape. Can point at any OpenAI-compatible
    embedding service. `api_key=None` leaves the stored key unchanged;
    `api_key=""` clears it."""
    url: str = Field(min_length=1)
    model: str = "auto"
    api_key: str | None = None


SearchProviderId = Literal[
    "coelho_search",
    "linkup",
    "you",
    "parallel",
    "exa",
    "geekflare",
    "tavily",
    "tinyfish",
    "firecrawl",
    "serper",
    "jina",
    "serpapi",
]


class SearchProviderBody(BaseModel):
    api_key: str | None = Field(default = None, max_length = 4096)
    # Used by Custom; omitted or null leaves the saved endpoint unchanged, and
    # an empty string clears the override and falls back to deployment config.
    endpoint: str | None = Field(default = None, max_length = 2048)


class SearchDefaultBody(BaseModel):
    provider_id: SearchProviderId | None = None


class SearchProviderView(BaseModel):
    provider_id: SearchProviderId
    name: str
    endpoint: str | None
    account_url: str | None
    available: bool
    key_required: bool
    has_key: bool
    key_source: Literal["user", "env"] | None
    key_last4: str | None
    connected: bool
    last_checked_at: float | None
    tools: list[str]
    last_test_error: str | None


class SearchSettingsView(BaseModel):
    default_provider: SearchProviderId | None
    providers: list[SearchProviderView]


class SearchTestResult(BaseModel):
    ok: bool
    provider_id: SearchProviderId
    latency_ms: int = Field(ge = 0)
    tools: list[str]
    error: str | None
