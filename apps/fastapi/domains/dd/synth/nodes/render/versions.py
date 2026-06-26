"""render — schema + template cache-invalidation markers."""
from __future__ import annotations


RENDER_SCHEMA_VERSION = "2.0-cookbook"
# added the write-path dedupe_and_align_sections pass
# (cross-section code recycling + misrouted-block omission). Bumped so
# the render cache invalidates and chapters re-render through the new
# pass.
RENDER_TEMPLATE_VERSION = "v3-dedup-align-2026-05-29"

# Same algorithm as `synth/vault.py:_hash_block` — 16-hex SHA-256 prefix.
# MUST match or the audit will false-fail.
HASH_ALGO = "sha256"
