"""elasticsearch keys — index names shared across modules.

Both names are consumed outside `service.py` (retriever, query,
ingestion, transcript, es_index, admin router), so they live here per
the §2 keys.py trigger — not in `params.py`. Kept verbatim from the
deprecated names (`helpers.py:L1958, L1977`) so re-ingesting from an
existing cluster is a no-op.
"""
from __future__ import annotations


INDEX_METADATA = "coelhonexus-youtube-metadata"
INDEX_TRANSCRIPTIONS = "coelhonexus-youtube-transcriptions"
