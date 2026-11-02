"""infra/qdrant — shared async Qdrant client.

YCS owns the consumers (its own collections) but the client, env
config, request models and the BM25 sparse-embedding class live here so no
domain forks the wiring."""
from . import params, service


__all__ = ["params", "service"]
