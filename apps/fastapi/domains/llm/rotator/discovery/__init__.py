"""Provider discovery — live free-tier model fan-out across providers."""
from .service import (
    flat_alive_list,
    list_all_alive_models,
    list_all_alive_models_sync,
    list_provider_free_models,
    missing_required_keys,
    probe_provider_key,
    required_providers,
)
from .types import DiscoveryRecord, ProviderConfig

__all__ = [
    "list_all_alive_models",
    "list_all_alive_models_sync",
    "list_provider_free_models",
    "probe_provider_key",
    "required_providers",
    "missing_required_keys",
    "flat_alive_list",
    "DiscoveryRecord",
    "ProviderConfig",
]
