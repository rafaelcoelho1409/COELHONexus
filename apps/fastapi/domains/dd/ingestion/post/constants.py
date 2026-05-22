"""Constants for post-ingest normalization."""
import re


MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000
SPLIT_MIN_SECTION_BYTES = 300       # raised from 64; MLflow p10 is ~2.5 KB

# Matches: "Source: https://..." on its own line. The modern llms-full.txt
# convention (Mintlify, Streamlit, etc.) emits these immediately under each
# original page's H1 as the canonical original-page-boundary signal.
_SOURCE_LINE_RE = re.compile(
    r'^Source:\s+(https?://\S+)\s*$', re.MULTILINE,
)
_SOURCE_MIN_MARKERS = 3             # below this count, format isn't trustworthy
