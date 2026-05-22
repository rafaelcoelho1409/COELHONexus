"""Vault sentinelization subpackage — re-exports all public names."""
from .constants import (
    _VAULT_HASH_LEN,
    _SENTINEL_FORMAT_VERSION,
    _HASH_ALGO,
    _SENTINEL_RE,
    _SENTINEL_HASH_RE,
)
from .types import (
    VaultEntry,
    VaultManifest,
    AuditReport,
)
from .service import (
    _hash_block,
    _make_sentinel,
    _parse_info_string,
    sentinelize_doc,
    materialize,
    audit_roundtrip,
    build_manifest,
)

__all__ = [
    "_VAULT_HASH_LEN",
    "_SENTINEL_FORMAT_VERSION",
    "_HASH_ALGO",
    "_SENTINEL_RE",
    "_SENTINEL_HASH_RE",
    "VaultEntry",
    "VaultManifest",
    "AuditReport",
    "_hash_block",
    "_make_sentinel",
    "_parse_info_string",
    "sentinelize_doc",
    "materialize",
    "audit_roundtrip",
    "build_manifest",
]
