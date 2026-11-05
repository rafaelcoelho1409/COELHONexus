"""render — schema + template cache-invalidation markers."""
from __future__ import annotations


RENDER_SCHEMA_VERSION = "2.0-cookbook"
# bump: added dedupe_and_align_sections (cross-section code recycling + misrouted-block fix).
# v4: NOISE_IDENTS gained file/files/path/paths (misroute false negative fix);
# stray-space slash-command normalization added.
# v6: cross-section code dedup DISABLED per user request — every subtopic
# shows its full code block even when recycled (no "Same code as ..." note).
RENDER_TEMPLATE_VERSION = "v6-no-crossref-dedup-2026-10-02"

# Same algorithm as `synth/vault.py:_hash_block` — 16-hex SHA-256 prefix.
# MUST match or the audit will false-fail.
HASH_ALGO = "sha256"

NORMALIZE_PROMPT_VERSION = "v3-2026-06-08"
