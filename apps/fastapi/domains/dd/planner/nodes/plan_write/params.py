"""plan_write tunables — schema/cache tags + title-case word sets."""
from __future__ import annotations


BLOB_PREFIX    = "planner"
DESCRIPTION_MAX_CHARS = 400
TITLE_MAX_WORDS       = 10
SLUG_MAX_WORDS        = 6

# Words that should stay lowercase in titles unless first/last. Keep
# tight — sanitization is best-effort, not authoritative.
TITLE_LOWERCASE = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "is", "of", "on", "or", "the", "to", "vs", "with",
})

# Words that should always stay UPPERCASE (acronyms commonly in docs).
TITLE_UPPERCASE = frozenset({
    "api", "apis", "cli", "cdk", "css", "html", "http", "https",
    "io", "json", "jwt", "k8s", "rpc", "sdk", "sql", "ssl", "tcp",
    "tls", "url", "uri", "ui", "uuid", "xml", "yaml", "ai", "llm",
    "ml", "nlp", "ux", "ide", "orm", "rest", "ssh",
})
