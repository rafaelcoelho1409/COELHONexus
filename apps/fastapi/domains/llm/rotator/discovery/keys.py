from __future__ import annotations


# Gemini free-tier model-name prefixes. Returned by Google's /v1beta/models
# endpoint as `models/<id>`; the Gemini filter accepts only names starting
# with one of these.
_GEMINI_FREE_NAME_PREFIXES: tuple[str, ...] = (
    "models/gemini-2.5-pro",
    "models/gemini-2.5-flash",
    "models/gemini-2.5-flash-lite",
    "models/gemini-embedding",
)
