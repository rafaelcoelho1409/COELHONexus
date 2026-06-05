"""vault — loose tunables (hash truncation + pedagogy scorer's mainstream
language set)."""
from __future__ import annotations


VAULT_HASH_LEN = 16


# Pedagogy scorer (Ship #2, 2026-05-24) — mainstream-language bonus set.
PEDAGOGY_LANGS = frozenset({
    "python", "py", "javascript", "js", "typescript", "ts", "go",
    "rust", "java", "c", "cpp", "c++", "ruby", "php", "shell", "bash",
})
