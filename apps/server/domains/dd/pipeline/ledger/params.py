# Two code blocks of one language are variants of the same example when their identifiers overlap at least this much (Jaccard).
VARIANT_JACCARD = 0.6
# Identifiers shorter than this carry no signal when comparing two blocks.
MIN_IDENTIFIER_CHARS = 3
# In a ledger of at least this many blocks, a name used by more than this share of them does not tell examples apart.
MIN_BLOCKS_FOR_COMMON = 12
COMMON_NAME_RATIO = 0.3
# A canonical block scoring below this is not one of the best codes of its topic: it stays in the ledger but the chapter need not teach it.
MIN_TEACH_SCORE = 2.0
# A block this short is a fragment, not an example worth a step of its own.
TRIVIAL_LINES = 2
# The block sizes a tutorial step teaches best: the sweet spot, and the longest still readable as one step.
SWEET_SPOT_LINES = (5, 40)
LONG_LINES = 80
# The score of a block (higher = a better canonical variant).
SCORE_PARSES = 1.5
SCORE_IMPORTS = 0.5
SCORE_SWEET_SPOT = 1.0
SCORE_LONG = 0.4
SCORE_TRIVIAL = -1.0
SCORE_DEFINES = 0.5
SCORE_COMPLETE = 0.3
SCORE_DEPRECATED = -1.0
SCORE_PER_EXTRA_PAGE = 0.2
SCORE_EXTRA_PAGES_CAP = 0.6
# Languages whose blocks are run in a shell, written as configuration, or printed as output; every other language is code.
SHELL_LANGS = frozenset({"bash", "sh", "shell", "zsh", "console", "terminal", "powershell", "cmd"})
CONFIG_LANGS = frozenset({"json", "yaml", "yml", "toml", "ini", "env", "dotenv", "xml", "properties"})
OUTPUT_LANGS = frozenset({"text", "txt", "output", "log", "plaintext", "plain"})
# Reading speed: words of prose per minute, and seconds spent on a line of code (read and follow, not type).
WORDS_PER_MINUTE = 230
SECONDS_PER_CODE_LINE = 3.0
# What a tutorial chapter takes to read, estimated before it is written: words of explanation per step, words of introduction, and the lines of the capstone.
WORDS_PER_STEP = 60
INTRO_WORDS = 200
CAPSTONE_LINES_ESTIMATE = 40
# The chapter length the plan aims for and the longest it allows, in minutes of reading (a count that follows from the total is suggested, never forced).
TARGET_CHAPTER_MINUTES = 25.0
MAX_CHAPTER_MINUTES = 45.0
# The most blocks one summary for an agent lists, and how many lines of each it quotes.
SUMMARY_MAX_BLOCKS = 60
SUMMARY_PREVIEW_LINES = 3
