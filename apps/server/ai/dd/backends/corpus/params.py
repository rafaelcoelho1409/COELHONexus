ROUTE = "/corpus/"
DOCS_DIR = "/docs"
# Characters of a page one `read_file` can reach; a longer page is cut with a note, the agent works from summaries first.
MAX_PAGE_CHARS = 60_000
# Entries one `ls` returns; a corpus larger than this is searched with `grep` or `glob`.
LS_MAX_ENTRIES = 400
# Matches one `grep` returns.
GREP_MAX_MATCHES = 120
READ_ONLY_ERROR = "the corpus is read-only"
