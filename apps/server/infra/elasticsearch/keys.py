"""Elasticsearch index names shared across application modules.

Names consumed outside `service.py` live here per the §2 keys.py
trigger — not in `params.py`.
"""
from __future__ import annotations


INDEX_METADATA = "coelhonexus-ycs-metadata"
INDEX_TRANSCRIPTIONS = "coelhonexus-ycs-transcriptions"
INDEX_BD_INGESTION = "coelhonexus-bd-ingestion"
