"""MinIO async storage adapter + per-framework manifest/body store."""
from __future__ import annotations

from .entities import ContentType, ManifestEntry
from .keys import (
    artifact_key,
    framework_prefix,
    live_manifest_key,
    manifest_key,
    page_key,
    raw_page_key,
    vault_manifest_key,
    vault_sentinelized_key,
)
from .service import (
    MinIOStorage,
    Store,
    get_storage,
    read_framework_manifest,
    read_framework_page,
    read_live_manifest,
)

__all__ = [
    "ContentType",
    "ManifestEntry",
    "MinIOStorage",
    "Store",
    "artifact_key",
    "framework_prefix",
    "get_storage",
    "live_manifest_key",
    "manifest_key",
    "page_key",
    "raw_page_key",
    "read_framework_manifest",
    "read_framework_page",
    "read_live_manifest",
    "vault_manifest_key",
    "vault_sentinelized_key",
]
