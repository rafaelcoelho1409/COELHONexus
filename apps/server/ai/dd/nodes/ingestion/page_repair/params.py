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
