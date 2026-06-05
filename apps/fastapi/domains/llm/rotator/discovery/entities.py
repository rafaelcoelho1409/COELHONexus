from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FreeFilter(Enum):
    """Free-tier filter selector for a provider; dispatched to a predicate in domain.py."""
    ALL               = "all"
    MISTRAL           = "mistral"
    GEMINI            = "gemini"
    SAMBANOVA_PRICING = "sambanova_pricing"
    ALWAYS_FALSE      = "always_false"


@dataclass(frozen = True)
class DiscoveryRecord:
    """One model entry as observed at fetch time."""
    provider:   str
    model_id:   str          # canonical id used by LiteLLM's `<provider>/<model_id>`
    fetched_at: float        # unix seconds
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
    # `required=True` → key is MANDATORY; the whole DD pipeline can't run
    # without it (e.g. NIM hosts embeddings + reranking).
    required:       bool = False
