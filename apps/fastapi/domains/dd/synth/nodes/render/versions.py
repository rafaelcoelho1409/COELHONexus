"""render — schema + template cache-invalidation markers."""
from __future__ import annotations


RENDER_SCHEMA_VERSION = "2.0-cookbook"
# bump: added dedupe_and_align_sections (cross-section code recycling + misrouted-block fix).
# v4: NOISE_IDENTS gained file/files/path/paths (misroute false negative fix);
# stray-space slash-command normalization added.
RENDER_TEMPLATE_VERSION = "v4-noise-idents-slash-fix-2026-09-05"

# Same algorithm as `synth/vault.py:_hash_block` — 16-hex SHA-256 prefix.
# MUST match or the audit will false-fail.
HASH_ALGO = "sha256"
