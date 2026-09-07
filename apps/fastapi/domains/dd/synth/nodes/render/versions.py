"""render — schema + template cache-invalidation markers."""
from __future__ import annotations


RENDER_SCHEMA_VERSION = "2.0-cookbook"
# bump: added dedupe_and_align_sections (cross-section code recycling + misrouted-block fix).
# v4: NOISE_IDENTS gained file/files/path/paths (misroute false negative fix);
# stray-space slash-command normalization added.
# v5: fence info-string sanitized for display (strips leaked Mintlify/MDX
# JSX attrs like `theme={null}`) — vault storage/hashing untouched.
RENDER_TEMPLATE_VERSION = "v5-fence-info-sanitize-2026-09-06"

# Same algorithm as `synth/vault.py:_hash_block` — 16-hex SHA-256 prefix.
# MUST match or the audit will false-fail.
HASH_ALGO = "sha256"
