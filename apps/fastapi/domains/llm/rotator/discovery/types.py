from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class FreeFilter(Enum):
    """Free-tier filter selector for a provider; dispatched to a predicate in service.py."""
    ALL = "all"
    MISTRAL = "mistral"
    GEMINI = "gemini"
    SAMBANOVA_PRICING = "sambanova_pricing"
    ALWAYS_FALSE = "always_false"


@dataclass(frozen = True)
class DiscoveryRecord:
    """One model entry as observed at fetch time."""
    provider: str
    model_id: str          # canonical id used by LiteLLM's `<provider>/<model_id>`
    fetched_at: float      # unix seconds
    raw: dict = field(default_factory = dict)  # full provider response item


@dataclass(frozen = True)
class ProviderConfig:
    name: str
    url: str
    key_env: str
    auth_style: str                            # "bearer" | "query-key"
    response_shape: str                        # "openai" | "gemini"
    free_filter: FreeFilter
    enabled: bool = True
    required: bool = False                      # key is MANDATORY (e.g. NIM powers
                                               # embeddings + reranking — the whole
                                               # DD pipeline can't run without it)