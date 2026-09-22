"""Settings — bounded context. External-endpoint configuration + credential store.

`credentials` is the MinIO-backed Fernet store for user-supplied secrets
(endpoint API keys, source tool keys) plus the `llm_endpoint` /
`embedding_endpoint` settings blobs the Settings page persists.
`chat` / `embeddings` are thin OpenAI-compatible adapters over the
user-configured external endpoints — the ONLY connections Nexus makes to
external LLMs / embedding models. No bundled gateway, no provider fan-out.
"""
from __future__ import annotations
from . import chat, credentials, embeddings


__all__ = ["chat", "credentials", "embeddings"]
