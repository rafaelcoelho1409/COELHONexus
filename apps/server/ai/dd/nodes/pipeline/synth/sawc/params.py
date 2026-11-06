"""sawc — tunable scalars (v2 cookbook schema limits + draft-of-N + memory)."""
from __future__ import annotations

import builtins


# Identifiers that carry no signal in a code block or its prose.
IDENT_STOPWORDS = frozenset({
    "self", "cls", "str", "int", "bool", "list", "dict", "set", "tuple",
    "none", "true", "false", "return", "import", "from", "async", "def",
    "class", "yield", "raise", "with", "for", "while", "else", "elif",
    "try", "except", "finally", "not", "and", "the", "this", "that",
    "data", "key", "val", "value", "result", "item", "items", "args",
    "kwargs", "name", "type", "obj", "object", "func", "function",
    "arg", "params", "ctx", "context", "request", "response", "main",
})

# Python builtins — a code block that uses one defines nothing of its own.
BUILTIN_NAMES = frozenset(dir(builtins))

# Validation issues that are advisory (the writer may keep the draft).
SOFT_ISSUE_PREFIXES = (
    "subheading↔code mismatch",
    "explanation↔code mismatch",
    "subtopics has only ",
)

# Relevance rank of a routed source (lower wins).
REL_RANK = {"primary": 0, "supporting": 1}
RELEVANCE_RANK = {"primary": 0, "supporting": 1, "tangential": 2}

# Visible-Vault budget: per vault entry and per chapter.
MAX_VAULT_CHARS_PER_ENTRY = 6_000
MAX_VAULT_CHARS_TOTAL     = 60_000

CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)

# Non-breaking hyphen (U+2011) and no-break spaces the model likes
# ("low‑level", "top‑level") swapped for plain characters: they break
# heading anchors, text search and identifier matching in the book.
TYPOGRAPHY = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " ",
})


# Max concurrent section writers (and top-up runs: TOPUP_CONCURRENCY) of one chapter.
CONCURRENCY = 8
# A model call that fails for a transient provider reason is asked again this many times in all, waiting `RETRY_WAIT_S` x attempt between.
ASK_ATTEMPTS = 2
RETRY_WAIT_S = 4.0
# Writer runs one section gets: the first, and one more with the hard issues the first left (or at half the vault budget after a context overflow).
WRITER_ATTEMPTS = 2

# v2 schema : cookbook-style nested structure where
# each Subtopic is a 1:1 pair of (explanation, code block).
SUBTOPICS_MIN = 3
SUBTOPICS_MAX = 12
SUBHEADING_MIN_WORDS = 1    # API-reference subtopics are named by a bare identifier ('asyncio.BoundedSemaphore') — a 2-word floor rejected whole drafts over it
SUBHEADING_MAX_WORDS = 10
EXPLANATION_WORDS_MIN = 8
EXPLANATION_WORDS_MAX = 80
INTRO_CHARS_MIN = 20
INTRO_CHARS_MAX = 400


# Legacy paragraph-mode bounds (kept for backwards-compat).
PARAGRAPHS_MIN = 2
PARAGRAPHS_MAX = 12
PARAGRAPH_CHARS_MIN = 80
PARAGRAPH_CHARS_MAX = 1800
HEADING_MIN_WORDS = 1    # matches outline_sdp — single-word module/API names are valid H2s
HEADING_MAX_WORDS = 8
CODE_REFS_MAX = 30
CITATIONS_MIN = 0
CITATIONS_MAX = 12
CITATION_CLAIM_CHARS_MIN = 6
CITATION_CLAIM_CHARS_MAX = 400
PLACEMENT_HINT_CHARS_MIN = 4
PLACEMENT_HINT_CHARS_MAX = 200
MEMORY_TERMS_MIN = 0
MEMORY_TERMS_MAX = 12
MEMORY_TERM_CHARS_MIN = 2
MEMORY_TERM_CHARS_MAX = 80
MEMORY_SUMMARY_CHARS_MIN = 40
MEMORY_SUMMARY_CHARS_MAX = 600

BLOB_PREFIX = "synth"

# Section-relevance code routing. The digest routes a source to the section(s)
# it is mostly about, so every block of a multi-topic guide page lands in ONE
# section and the others — given 0 routed hashes — go prose-only even though the
# chapter holds on-topic code (elasticsearch-python ch-11: 31 blocks → s1, 0 → s2-s4).
# A section with too few routed blocks also considers chapter-wide blocks whose
# vocabulary the section's own text covers at least this much …
RELEVANT_PAD_MIN_SCORE  = 0.35
# … and only leaves prose mode when this many such blocks exist (< this and
# the section stays prose rather than being handed loosely related code).
RELEVANT_PAD_MIN_BLOCKS = 3
RELEVANCE_QUERY_MAX_CHARS = 30000

# A subtopic whose prose (subheading + explanation) shares at most REALIGN_MAX_CUR identifiers SPECIFIC to the chapter
# (not found in >= REALIGN_GENERIC_DF of its code bank: asyncio, await, tasks ...) with its code block got the wrong hash
# (asyncio ch-03: "Bounded Queues for Back-Pressure" over the Event example). It is swapped for the unused allowed block
# sharing at least REALIGN_MIN_ALT specific identifiers and REALIGN_MIN_GAIN more than the current one. The filter needs a
# bank of REALIGN_MIN_BANK blocks; smaller banks never swap. (Dry run on 3 stored chapters: only the ch-03 pair swaps.)
REALIGN_MAX_CUR = 1
REALIGN_MIN_ALT = 4
REALIGN_MIN_GAIN = 4
REALIGN_GENERIC_DF = 0.35
REALIGN_MIN_BANK = 8

# Coverage top-up (sawc_write): after the sections are written, each section whose digest-routed code blocks went unused
# gets ONE extra call that adds subtopics for them (asyncio ch-01 used 14 of 35 routed blocks, ch-05 19 of 35: a 3-4
# section outline x 4-5 subtopics caps what the writer reaches). Verbatim blocks only; every guard still runs after it.
TOPUP_ENABLED = True
TOPUP_MIN_UNUSED = 2          # fewer unused routed blocks than this: not worth a call
TOPUP_MAX_NEW = 8             # subtopics added per section per call (6 -> 8: a section routed 24 blocks, asyncio ch-05)
TOPUP_CONCURRENCY = 3
TOPUP_MAX_TOKENS = 4000
TOPUP_TIMEOUT_S = 150.0
TOPUP_MAX_BLOCK_CHARS = 1500  # of each block shown to the model
TOPUP_MAX_FACTS = 14
# A top-up call that fails or whose answer holds nothing valid is asked again (asyncio ch-04: a 147 s Rotator 504 on the source top-up, and an answer
# the replay shows is fine on a second ask, left the `run()` helper block the printed gather example calls out of the chapter).
TOPUP_ATTEMPTS = 2
# Rounds of the coverage top-up (each section that gained subtopics and still has unused routed blocks and room is asked again).
TOPUP_ROUNDS = 2
# A section whose digest-routed bank is smaller than BANK_MIN_SIZE is padded up to BANK_PAD_TO blocks (relevant ones first, then the chapter's most instructive).
BANK_MIN_SIZE = 6
BANK_PAD_TO = 20
# A section that lost every draft is written once more after the other sections finish, with a lighter vault in the prompt.
LATE_RETRY_ENABLED = True
LATE_RETRY_VAULT_CHARS = 24_000
# A section written without any code (prose mode): at most ceil(key facts / this) subtopics, never fewer than
# SUBTOPICS_MIN (asyncio ch-08: 5 subtopics out of 4 key facts restated the same sentence).
PROSE_FACTS_PER_SUBTOPIC = 1.5

# Source top-up (after the code top-up): sources the digest routed to a section but the chapter never cites — API pages whose
# facts have no code (asyncio ch-05: get_running_loop, get_event_loop, EventLoop, loop implementations; ch-01: current_task,
# all_tasks, eager task factory) — get prose subtopics grounded in their key facts.
TOPUP_SRC_ENABLED = True
TOPUP_SRC_MAX_NEW = 4          # prose subtopics added per section per call (one may cover several sources)
TOPUP_SECTION_MAX = 16         # a topped-up section never grows past this many subtopics
TOPUP_SRC_EXTRA = 3            # the source top-up may take a section this many subtopics past TOPUP_SECTION_MAX: one prose subtopic may cover several uncited sources, and a section
                               # that absorbed many sources (langgraph ch-07: s1 held 11 provider pages at 16 subtopics) would otherwise never cite the code-less ones
TOPUP_SRC_MIN_FACTS = 1        # a source needs at least this many key facts to be offered
TOPUP_SRC_GROUND_TOKENS = 2    # prose must share this many words with the facts it covers
TOPUP_SRC_CITE_TOKENS = 3      # shared words that make an uncited-but-used source count as cited
TOPUP_SRC_MIN_WORDS = 18       # a topped-up subtopic shorter than this is dropped (asyncio ch-07: 10-19 word fragments of one page)
# A source the chapter cites can still be left unsaid: asyncio ch-04 cited 7 of 7 pages with one-line claims while 10 of 31 key
# facts (the shell-injection warning, the garbage-collection kill, `limit`, `send_signal`) appeared nowhere. A key fact counts as
# covered when at least TOPUP_FACT_COVERED of its words occur in the chapter prose (code excluded); a cited source with at least
# TOPUP_FACT_MIN_GAP uncovered facts goes through the source top-up with just those facts. Stored asyncio chapters: 4-32% of facts.
TOPUP_FACT_COVERED = 0.5
TOPUP_FACT_MIN_TOKENS = 3      # a shorter fact is too small to judge
TOPUP_FACT_MIN_GAP = 2
# Citations of a topped-up section may exceed the writer's CITATIONS_MAX (asyncio ch-05: s1 had 12 and the 4 new sources
# were dropped by the cap, so the coverage gate kept failing); the persisted Section has no such limit.
# A routed block that is a near-copy of a printed one (same command with another flag, same snippet for another client) counts as covered, in the coverage
# gate and in the top-up (fastmcp ch-01: 36 of the 60 unprinted routed blocks overlapped a printed block by >= 0.6, so the 60% bar was unreachable and the model
# rejected the rest as duplicates).
NEARDUP_JACCARD = 0.7
NEARDUP_MIN_TOKENS = 5

TOPUP_CITATIONS_MAX = 30


# Prose echoes. A copy of a code block that loses it to the subtopic describing it is dropped when its explanation shares at least
# ECHO_JACCARD of the winner's words (stored asyncio pairs: copies 0.26-0.96, a prose-only mismatch 0.04). A prose subtopic that
# repeats ANY other subtopic of the chapter is dropped only when it is near-verbatim, PROSE_ECHO_JACCARD (a verbatim restatement
# scored 0.96; two one-word-apart reference entries, WindowsSelector/WindowsProactor policy, 0.64 and both teach something).
ECHO_JACCARD = 0.25
PROSE_ECHO_JACCARD = 0.7

# A section whose digest-routed bank is smaller than the writer minimum borrows blocks routed to OTHER sections only when it would
# otherwise hold fewer than this many (asyncio ch-05: a 5-block section borrowed 15 blocks from the 24-block one, printed them first,
# and the owner's copies were dropped as repeats).
BANK_SHARE_BELOW = 3
