"""Elasticsearch index names shared across application modules.

Names consumed outside `service.py` live here per the §2 keys.py
trigger — not in `params.py`.
"""
from __future__ import annotations


INDEX_METADATA = "coelhonexus-ycs-metadata"
INDEX_TRANSCRIPTIONS = "coelhonexus-ycs-transcriptions"
INDEX_BD_INGESTION = "coelhonexus-bd-ingestion"
INDEX_YCS_RAG_PASSAGES = "coelhonexus-ycs-rag-bm25-passages"
INDEX_BD_RAG_PASSAGES = "coelhonexus-bd-rag-bm25-passages"

PHYSICAL_INDEX_METADATA = f"{INDEX_METADATA}-v1"
PHYSICAL_INDEX_TRANSCRIPTIONS = f"{INDEX_TRANSCRIPTIONS}-v1"
PHYSICAL_INDEX_BD_INGESTION = f"{INDEX_BD_INGESTION}-v1"
PHYSICAL_INDEX_YCS_RAG_PASSAGES = f"{INDEX_YCS_RAG_PASSAGES}-v1"
PHYSICAL_INDEX_BD_RAG_PASSAGES = f"{INDEX_BD_RAG_PASSAGES}-v1"

PHYSICAL_INDEX_BY_ALIAS = {
    INDEX_METADATA: PHYSICAL_INDEX_METADATA,
    INDEX_TRANSCRIPTIONS: PHYSICAL_INDEX_TRANSCRIPTIONS,
    INDEX_BD_INGESTION: PHYSICAL_INDEX_BD_INGESTION,
    INDEX_YCS_RAG_PASSAGES: PHYSICAL_INDEX_YCS_RAG_PASSAGES,
    INDEX_BD_RAG_PASSAGES: PHYSICAL_INDEX_BD_RAG_PASSAGES,
}

ALIAS_BY_PHYSICAL_INDEX = {
    physical_index: alias
    for alias, physical_index in PHYSICAL_INDEX_BY_ALIAS.items()
}
