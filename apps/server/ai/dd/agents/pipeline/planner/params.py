"""Limits that belong to the planner agents' answers: the schemas validate against them and the prompts quote them."""

# Per-page distillate (page_distiller).
SUMMARY_WORDS_MIN = 8
SUMMARY_WORDS_MAX = 60
KEY_TERMS_MIN = 3
KEY_TERMS_MAX = 8
KEY_TERM_CHARS_MIN = 2
KEY_TERM_CHARS_MAX = 80

# Chapter proposals (chapter_architect, section_analyst).
PROPOSALS_MIN = 4
PROPOSALS_MAX = 30   # was 18, which capped large corpora into mega-chapters
SECTION_PROPOSALS_MAX = 12
TITLE_MIN_WORDS = 1
TITLE_MAX_WORDS = 8
GENERIC_TITLES = frozenset({
    "introduction", "intro", "overview", "conclusion", "background", "about", "summary",
    "references", "reference", "foundations", "foundation", "miscellaneous", "misc",
    "other", "others", "general", "appendix", "index", "preface", "epilogue",
})
DESCRIPTION_CHARS_MIN = 20
DESCRIPTION_CHARS_MAX = 400
CONCEPTS_MIN = 3
CONCEPTS_MIN_ACCEPT = 2
CONCEPTS_MAX = 15
CONCEPT_CHARS_MIN = 2
CONCEPT_CHARS_MAX = 80

# Where the agents' skills live, relative to `ai/dd/skills/`.
SKILL_STAGE = "pipeline/planner"
