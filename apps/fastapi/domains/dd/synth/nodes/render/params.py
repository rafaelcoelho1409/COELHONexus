"""render — tunables (vault-hash truncation + within-chapter dedup/align
heuristics)."""
from __future__ import annotations


# Same algorithm as `synth/vault.py:_hash_block` — 16-hex SHA-256 prefix.
VAULT_HASH_LEN = 16

# Dedup only bodies with real heft — tiny one-liners (e.g.
# `claude --version`) recur legitimately and must NOT be cross-referenced
# away.
DEDUP_MIN_LINES = 3
DEDUP_MIN_CHARS = 80
# Mismatch needs enough identifiers to judge + a clean zero overlap.
MISMATCH_MIN_CODE_IDENTS = 4

# Noise identifiers excluded from overlap scoring.
NOISE_IDENTS = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "import", "async",
    "await", "def", "class", "return", "none", "true", "false", "str",
    "int", "self", "get", "set", "use", "run", "via", "your", "null",
    "var", "let", "const", "new", "function", "type", "name", "value",
    "data", "code",
})

BLOB_PREFIX = "synth"
