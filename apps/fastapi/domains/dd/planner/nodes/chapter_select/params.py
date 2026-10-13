"""chapter_select tunables — greedy coverage thresholds + cache tag."""
from __future__ import annotations


BLOB_PREFIX = "planner"

# Greedy coverage tuning.
CONFIDENCE_THRESHOLD = 0.5
COVERAGE_TARGET      = 0.95
MIN_DOCS_PER_CHAPTER = 3   # prune chapters below this unless pinned
MIN_KEPT_CHAPTERS    = 3   # restore lowest-pruned if kept < this

# Placement consolidation — nothing may fall out of the plan silently.
# A "family" is one parent documentation page (all virtual sub-pages of a
# Sphinx autodoc page share its URL).
TORN_FAMILY_MIN_DOCS      = 8      # families smaller than this are never re-homed wholesale
TORN_FAMILY_TOP2_SHARE    = 0.90   # two chapters must hold ≥ this share of the family …
TORN_FAMILY_MIN_MINOR     = 0.25   # … and the smaller of them ≥ this share (a genuine split)
FAMILY_DOMINANCE          = 0.50   # a weak/unplaced doc follows its family's chapter above this share …
FAMILY_MIN_STRONG_SHARE   = 0.50   # … but only if at least this share of the whole family is confidently placed

# Family harmonization — sibling sub-pages of ONE reference page stay together.
# Only ANCHORED families count (virtual sub-pages split out of one page, e.g.
# asyncio-task.html → create_task / TaskGroup / sleep …): on flat sites and on
# single-URL dumps (llms-full.txt: every doc shares one URL) the URL says nothing
# about topical kinship, and harmonizing there would pour a whole corpus into one
# chapter. A doc outside its family's majority chapter moves there only when the
# assigner itself rated that chapter a plausible home for it.
FAMILY_HARMONIZE_MIN_DOCS  = 3      # smaller families are left to per-doc scores
FAMILY_HARMONIZE_MAX_SHARE = 0.35   # a "family" bigger than this share of the corpus is not a page
FAMILY_HARMONIZE_MAJORITY  = 0.60   # a chapter must hold ≥ this share of the family's placed docs
HARMONIZE_MIN_CONF         = 0.30   # a minority doc moves only if the majority chapter scored it ≥ this …
HARMONIZE_MAX_GAP          = 0.40   # … and its current chapter beats that score by at most this

# Oversize split — a chapter holding more than SPLIT_MAX_SHARE of the corpus is
# split by ONE LLM call over family/doc units (see schemas.SplitPlan). The prompt
# rule "split above 40%" is only a request; this is the guarantee.
SPLIT_MAX_SHARE      = 0.30
SPLIT_MEDIAN_RATIO   = 2.0   # … AND bigger than this × the median chapter (a 5-chapter plan at 30.3% is not lopsided)
SPLIT_MIN_TOTAL_DOCS = 24    # tiny corpora legitimately have one big chapter
SPLIT_MIN_CHAPTER_DOCS = 12
SPLIT_MAX_PARTS      = 4
GENERIC_TITLES = frozenset({   # same one-word generics chapter_propose refuses
    "introduction", "intro", "overview", "conclusion", "background", "about", "summary",
    "references", "reference", "foundations", "foundation", "miscellaneous", "misc",
    "other", "others", "general", "appendix", "index", "preface", "epilogue",
})
SPLIT_MAX_TOKENS     = 3000
SPLIT_TIMEOUT_S      = 120.0
SPLIT_UNIT_SUMMARY_CHARS = 220
SPLIT_SETTLE_CAP_S   = 130.0   # cap of the Rotator-recovery wait before the split call(s)

# Foundations chapter — cross-cutting single pages (overview/landing, platform
# support, debugging, threading notes …) have no topical home and used to be
# scattered into whichever chapter scored them highest. doc_distill labels each
# doc's `role`; those labelled below, and standing alone on their own page, are
# collected into one front-matter chapter (pinned first by order_chapters).
FOUNDATIONS_ROLES      = ("overview", "cross_cutting")
FOUNDATIONS_MIN_DOCS   = 4      # fewer than this → no chapter (docs stay where they are)
FOUNDATIONS_MAX_SHARE  = 0.15   # never more than this share of the corpus (keeps role over-labelling harmless)
# Catalog guard: ≥ this many Foundations candidates under ONE URL directory are a list of
# like pages (provider/integration/loader landing pages — deepagents/langgraph put 7-9
# of them into Foundations), not orientation material. Skipped for a directory holding
# more than the share of the corpus below: on a flat site (every page in /library/) the
# directory says nothing about kinship.
FOUNDATIONS_CATALOG_MIN       = 3
FOUNDATIONS_CATALOG_MAX_SHARE = 0.35
FOUNDATIONS_TITLE      = "Foundations and Diagnostics"
FOUNDATIONS_DESCRIPTION = (
    "Orientation and cross-cutting material: framework overview, platform "
    "support, debugging, diagnostics and runtime notes that apply across topics."
)

# Rescue of unselected proposals follows the assigner's own evidence: when weak/
# unplaced docs trigger a rescue (≥ MIN_DOCS_PER_CHAPTER of them), docs that
# STRICTLY prefer that proposal (it is their argmax at ≥ PREFER_MIN_CONF and beats
# their current chapter's score) move with them. Preference alone never triggers a
# rescue: replayed on stored plans it added a chapter to deepagents, langchain and
# langgraph, whose plans were fine.
PREFER_MIN_CONF  = 0.70

# Gap chapter whose pages share no common page title (a single-URL dump, e.g. an
# llms-full.txt split per heading) is not "about" any one page: it becomes a generic
# appendix, ordered last, instead of a chapter named after one arbitrary page title.
GAP_FAMILY_TITLE_SHARE = 0.50   # the commonest page title must cover ≥ this share of the family
GAP_APPENDIX_TITLE     = "Additional Topics"
GAP_APPENDIX_LISTED    = 8      # member titles quoted in the appendix description
