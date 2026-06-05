"""Vault sentinelization subpackage — re-exports public surface."""
from .domain import (
    audit_roundtrip,
    build_manifest,
    format_entries_for_prompt,
    format_entry_for_prompt,
    materialize,
    rank_hashes_by_pedagogy,
    score_entry_pedagogy,
    sentinelize_doc,
)
from .params import VAULT_HASH_LEN
from .patterns import SENTINEL_HASH_RE, SENTINEL_RE
from .schemas import AuditReport, VaultEntry, VaultManifest
from .service import get_or_build_source_vault
from .versions import HASH_ALGO, SENTINEL_FORMAT_VERSION


__all__ = [
    "AuditReport",
    "HASH_ALGO",
    "SENTINEL_FORMAT_VERSION",
    "SENTINEL_HASH_RE",
    "SENTINEL_RE",
    "VAULT_HASH_LEN",
    "VaultEntry",
    "VaultManifest",
    "audit_roundtrip",
    "build_manifest",
    "format_entries_for_prompt",
    "format_entry_for_prompt",
    "get_or_build_source_vault",
    "materialize",
    "rank_hashes_by_pedagogy",
    "score_entry_pedagogy",
    "sentinelize_doc",
]
