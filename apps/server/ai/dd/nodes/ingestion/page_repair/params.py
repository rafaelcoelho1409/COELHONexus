MIN_PATCH_CONFIDENCE = 0.90
MIN_TITLE_CONFIDENCE = 0.85
MAX_TITLE_CHARS = 120
PATCHABLE_CATEGORIES = frozenset({
    "duplicate_boilerplate",
    "duplicate_section",
    "malformed_heading",
    "markdown_formatting",
    "generated_title",
    "encoding_artifact",
})
# A repair in these categories changes syntax, never wording: its replacement keeps every word of its target.
WORD_PRESERVING_CATEGORIES = frozenset({
    "malformed_heading",
    "markdown_formatting",
})
