from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FreeFilter(Enum):
    ALL               = "all"
    MISTRAL           = "mistral"
    GEMINI            = "gemini"
    SAMBANOVA_PRICING = "sambanova_pricing"
    ALWAYS_FALSE      = "always_false"


@dataclass(frozen = True)
class DiscoveryRecord:
    provider:   str
    model_id:   str          # used by LiteLLM's `<provider>/<model_id>`
    fetched_at: float
    raw:        dict = field(default_factory = dict)


@dataclass(frozen = True)
class ProviderConfig:
    name:           str
    url:            str
    key_env:        str
    auth_style:     str                # "bearer" | "query-key"
    response_shape: str                # "openai" | "gemini"
    free_filter:    FreeFilter
    enabled:        bool = True
    required:       bool = False
