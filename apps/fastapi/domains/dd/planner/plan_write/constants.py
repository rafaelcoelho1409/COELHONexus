from __future__ import annotations

import re


_SCHEMA_VERSION = "1.0"
_PROMPT_VERSION = "v1-2026-05-18"
_BLOB_PREFIX    = "planner"
_DESCRIPTION_MAX_CHARS = 400
_TITLE_MAX_WORDS       = 10
_SLUG_MAX_WORDS        = 6

# Words that should stay lowercase in titles unless first/last. Keep
# tight — sanitization is best-effort, not authoritative.
_TITLE_LOWERCASE = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "is", "of", "on", "or", "the", "to", "vs", "with",
}
# Words that should always stay UPPERCASE (acronyms commonly in docs).
_TITLE_UPPERCASE = {
    "api", "apis", "cli", "cdk", "css", "html", "http", "https",
    "io", "json", "jwt", "k8s", "rpc", "sdk", "sql", "ssl", "tcp",
    "tls", "url", "uri", "ui", "uuid", "xml", "yaml", "ai", "llm",
    "ml", "nlp", "ux", "ide", "orm", "rest", "ssh", "tls",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")
