"""Provider discovery — live free-tier model fan-out across providers."""
from __future__ import annotations

from .config import PROVIDERS
from .domain import flat_alive_list
from .entities import DiscoveryRecord, FreeFilter, ProviderConfig
from .service import (
    list_all_alive_models,
    list_all_alive_models_sync,
    list_provider_free_models,
    missing_required_keys,
    probe_provider_key,
    required_providers,
)

__all__ = [
    "DiscoveryRecord",
    "FreeFilter",
    "PROVIDERS",
    "ProviderConfig",
    "flat_alive_list",
    "list_all_alive_models",
    "list_all_alive_models_sync",
    "list_provider_free_models",
    "missing_required_keys",
    "probe_provider_key",
    "required_providers",
]
