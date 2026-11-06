"""render — tunables (vault-hash truncation + within-chapter dedup/align
heuristics)."""
from __future__ import annotations

import os


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
    # Fixed 2026-09-05 — a "Checkpoint State Recovery" subtopic shipped
    # `rm file.txt / mv old.txt new.txt / cp source.txt dest.txt` (wholly
    # unrelated to the /rewind prose) because "file" was the only shared
    # identifier and wasn't filtered — generic enough to appear in almost
    # any prose about almost any code, so it carries no real relevance
    # signal on its own.
    "file", "files", "path", "paths",
})

BLOB_PREFIX = "synth"


# Languages where the LLM call is skipped — content is either plain
# text (no formatting concept) or terminal output (whitespace is
# semantic and any reformat is wrong).
SKIP_LANGS = frozenset({
    "", "text", "plaintext", "txt", "output", "console",
    "log", "json", "yaml", "yml", "xml", "diff", "patch",
    "mermaid", "ansi",
})

# Python gets AST validation (stdlib, no deps) — observed Mintlify MDX flattening strips function-body indent. Fail → retry LLM with error; second fail → keep best attempt.
PYTHON_LANGS = frozenset({"python", "py", "py3", "python3"})

# Max concurrent code normalizers.
CONCURRENCY = 8
# A vault entry longer than this is not normalized (cache blow-out + diminishing returns).
NORMALIZE_MAX_FENCE_CHARS = 12_000
# A model call that fails for a transient provider reason is asked again this many times in all, waiting `RETRY_WAIT_S` x attempt between.
ASK_ATTEMPTS = 2
RETRY_WAIT_S = 4.0


# Env flag — default ON. Set KD_RENDER_DROP_MISMATCH=false to keep a code block whose identifiers share none with its prose.
ENV_DROP_MISMATCH = "KD_RENDER_DROP_MISMATCH"


def drop_mismatch() -> bool:
    """Whether a misrouted code block is omitted at render time. A function so the module has no import-time env dependency."""
    return os.environ.get(ENV_DROP_MISMATCH, "true").lower() not in ("0", "false", "no")
