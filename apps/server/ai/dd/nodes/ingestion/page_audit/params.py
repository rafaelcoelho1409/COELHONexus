MAX_ATTEMPTS = 3
RETRY_WAIT_BASE_S = 20.0
MAX_PAGE_MARKDOWN_CHARS = 180_000
MAX_ISSUES_PER_PAGE = 24
MAX_EVIDENCE_CHARS = 2_000
# Findings about syntax: the ones a documentation-export convention can be mistaken for. A repeated block is not syntax,
# so a duplicate finding keeps its evidence whatever it quotes.
CONVENTION_CATEGORIES = frozenset({
    "markdown_formatting",
    "malformed_heading",
    "other",
})
# Findings that claim the evidence is repeated on the page: they hold only when a second copy exists (`domain.is_repeat`).
DUPLICATE_CATEGORIES = frozenset({
    "duplicate_boilerplate",
    "duplicate_section",
})
# Leads from the page scans (`domain.candidates`): the shortest block that counts as a repeat, how many leads go to the auditor, and how much
# text a lead about damaged characters quotes.
MIN_REPEAT_CHARS = 40
MAX_CANDIDATES = 12
EVIDENCE_WINDOW = 120
