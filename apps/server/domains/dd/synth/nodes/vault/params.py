"""vault — loose tunables (hash truncation + pedagogy scorer's mainstream
language set)."""
from __future__ import annotations


VAULT_HASH_LEN = 16


# Pedagogy scorer — mainstream-language bonus set.
PEDAGOGY_LANGS = frozenset({
    "python", "py", "javascript", "js", "typescript", "ts", "go",
    "rust", "java", "c", "cpp", "c++", "ruby", "php", "shell", "bash",
})


# Section-relevance matching — generic words that carry no topical signal.
RELEVANCE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "using", "use", "used", "this", "that", "from",
    "into", "over", "are", "can", "how", "what", "when", "which", "section",
    "sections", "overview", "details", "basics", "api", "apis", "python",
    "client", "documentation", "guide", "provides", "provide", "include",
    "includes", "including",
})
RELEVANCE_MIN_TOKEN_LEN = 3
