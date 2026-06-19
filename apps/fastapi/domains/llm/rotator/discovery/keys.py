from __future__ import annotations


# Google's /v1beta/models returns `models/<id>`; filter accepts only these prefixes.
_GEMINI_FREE_NAME_PREFIXES: tuple[str, ...] = (
    "models/gemini-2.5-pro",
    "models/gemini-2.5-flash",
    "models/gemini-2.5-flash-lite",
    "models/gemini-embedding",
)
