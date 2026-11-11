# A repair is optional (the audited page is already valid), so it gets fewer tries than the audit: every extra try on
# a failing provider holds the whole run open after the other pages are done.
MAX_ATTEMPTS = 2
RETRY_WAIT_BASE_S = 3.0
MIN_PATCH_CONFIDENCE = 0.90
MIN_AUDIT_CONFIDENCE = 0.95
MIN_TITLE_CONFIDENCE = 0.85
MAX_TITLE_CHARS = 120
PATCHABLE_CATEGORIES = frozenset({
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
# What the repairer's `check_patch` tool tells the model to do about each reason a patch is refused.
REJECTION_HINTS = {
    "source hash mismatch": "Copy source_sha256 from the request exactly in your answer.",
    "patch does not reference an accepted finding": "Use an issue_id from the supplied assessment, or drop the patch.",
    "patch evidence does not match its audited finding": "Copy the finding's evidence exactly as the assessment gives it.",
    "patch target does not overlap its audited evidence": "target_text must contain the finding's evidence, or lie inside it.",
    "finding category is not eligible for automatic repair": "Drop the patch; this category is review-only.",
    "confidence below the automatic-apply threshold": "Drop the patch unless you are at least 0.90 sure of it.",
    "evidence is not an exact source substring": "Drop the patch; the evidence is not on the page.",
    "target does not occur exactly once": "Extend target_text with the adjacent page text until it occurs exactly once.",
    "formatting patch removes words from the page": "Keep every word of the target in the replacement, or drop the patch.",
    "removing it would leave no copy of the text": "Drop the patch; this is the only copy on the page.",
    "patch target overlaps a fenced code block": "Drop the patch; code is never edited.",
    "patch takes the quote markers off part of a block quote": "Drop the patch; a quoted tab, card or callout is how the site exports it, and a quote is changed whole or not at all.",
    "surrounding context mismatch": "Copy context_before and context_after exactly from the page text around target_text.",
}
