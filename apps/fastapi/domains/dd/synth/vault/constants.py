"""Vault sentinelization — module-level constants."""
from __future__ import annotations

import re


# Module constants. Bump _SENTINEL_FORMAT_VERSION on any sentinel-shape
# change so MinIO-cached vaults from prior versions invalidate cleanly.
_VAULT_HASH_LEN          = 16
_SENTINEL_FORMAT_VERSION = 1
_HASH_ALGO               = "sha256-16"

# Matches the canonical sentinel shape this module emits. `lang` is
# optional (older blocks with `lang=""` skip the attr); hash MUST be
# exactly 16 hex chars; self-closing form is mandatory.
_SENTINEL_RE = re.compile(
    r'<code-ref hash="(?P<hash>[0-9a-f]{16})"(?: lang="(?P<lang>[^"]*)")?/>',
)
# Plain hash-only matcher used by `audit_roundtrip` to enumerate every
# sentinel-shaped token in an LLM output (whether or not it matches the
# vault — `invented` sentinels show up here).
_SENTINEL_HASH_RE = re.compile(r'<code-ref hash="([0-9a-f]{16})"')
