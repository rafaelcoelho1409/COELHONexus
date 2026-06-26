"""Identifier registries for the HN tool — per docs/CODE-CONVENTIONS.md §2.

`DEFAULT_TAGS` is what the service uses when the caller doesn't specify.
`VALID_TAGS` is the canonical Algolia-HN tag vocabulary — used as a guide in
the input-schema description so the LLM picks legal values (we don't strictly
validate; Algolia quietly ignores unknown tags).

Source: https://hn.algolia.com/api (the `tags` parameter)
"""
from __future__ import annotations


# the points + comment signal); comments are usually too granular to surface
# in a daily digest.
DEFAULT_TAGS: tuple[str, ...] = ("story",)


VALID_TAGS: tuple[str, ...] = (
    "story",        # any top-level post (default)
    "comment",      # individual comments
    "poll",         # poll posts
    "pollopt",      # poll options
    "show_hn",      # "Show HN:" posts (project launches)
    "ask_hn",       # "Ask HN:" posts (discussion threads)
    "front_page",   # currently on front page
)
