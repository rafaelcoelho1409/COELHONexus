"""Constants for post-ingest normalization."""
import re


MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000
SPLIT_MIN_SECTION_BYTES = 300       # raised from 64; MLflow p10 is ~2.5 KB

# Size-aware H2 sub-split — any section above this threshold that
# survived the H1 split + stub-drop + dedup gets re-split on H2 with the
# parent H1 prepended for context. Chosen empirically on Dask's
# llms-full.txt: 722 KB Changelog (one of the worst-case publisher
# bundles) splits into ~206 navigable per-version pages, while
# DataFrame/Futures/Configuration sections (104-139 KB) stay intact —
# they render fine and don't have a "splittable" H2 structure (one
# dominant H2 + many tiny stubs). Tuning above ~150 KB risks leaving
# slow-to-render pages; tuning below risks over-fragmenting
# already-coherent API references.
SPLIT_MAX_SECTION_BYTES = 150_000

# Matches: "Source: https://..." on its own line. The modern llms-full.txt
# convention (Mintlify, Streamlit, etc.) emits these immediately under each
# original page's H1 as the canonical original-page-boundary signal.
_SOURCE_LINE_RE = re.compile(
    r'^Source:\s+(https?://\S+)\s*$', re.MULTILINE,
)
_SOURCE_MIN_MARKERS = 3             # below this count, format isn't trustworthy
