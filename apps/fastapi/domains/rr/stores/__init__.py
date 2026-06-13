"""Store-specific I/O for the RR domain.

Per docs/CODE-CONVENTIONS.md §service: each store gets its own module
because each has a different driver setup (psycopg / neo4j / qdrant /
aioboto3). The orchestrator in `service.py` composes them.

  postgres.py   relational state (radar_scans · findings · seen · profiles)
  neo4j.py      paper / concept / author / source graph (MERGE by arxiv_id)
  qdrant.py     radar_papers vector collection + payload index ops
  minio.py      digest.json + per-paper extraction.json artifacts
"""
