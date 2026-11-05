"""Operational bounds and version for DD page normalization."""
NORMALIZER_VERSION = "dd-ingestion-agentic-v1"
JSON_TYPE = "application/json"
MARKDOWN_TYPE = "text/markdown; charset=utf-8"

# Characters kept from the sha256 of a candidate's frozen page inputs; names its graph thread.
DIGEST_CHARS = 24
# Progress statuses a candidate can be resumed from.
RESUMABLE_STATUSES = frozenset({"failed", "cancelled"})
# Fields of the framework manifest that a finalized candidate recomputes instead of carrying over.
MANIFEST_OWNED_FIELDS = frozenset(
    {
        "framework_slug",
        "entries",
        "page_count",
        "total_bytes",
        "ingested_at",
        "normalizer_version",
    }
)
# Sub-prefixes of a candidate's artifact prefix that a wipe deletes; its input pages are kept.
CANDIDATE_PARTS = ("pages/", "assessments/", "audit/", "vault/")
