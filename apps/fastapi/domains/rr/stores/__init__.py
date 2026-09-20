"""Store-specific I/O for the RR domain — MinIO/Neo4j/Postgres/Qdrant,
merged into one `service.py` per docs/CODE-CONVENTIONS.md §8 strict-merge
(each is an I/O adapter for one backend, same role, no feature-name
split). The orchestrator in `../service.py` composes them."""
from __future__ import annotations

from . import service
