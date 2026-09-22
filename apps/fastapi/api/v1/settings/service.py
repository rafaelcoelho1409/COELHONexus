"""settings service — endpoint view builders + save handlers shared by endpoints."""
from __future__ import annotations

import domains
from . import schemas

from dataclasses import asdict

# ---------------------------------------------------------------------------
# LLM endpoint — the OpenAI-compatible URL the Docs Distiller / YCS / RR
# apps call. This field is the single runtime source of truth for where it
# lives — point it at any OpenAI-compatible chat endpoint. Overrides the
# LLM_ENDPOINT_* / COELHO_LLM_* env defaults when set.
# ---------------------------------------------------------------------------

_ENDPOINT_KEY_ENV = "COELHO_LLM_API_KEY"


def _endpoint_view() -> dict:
    s = domains.settings.credentials.service.get_store().read_settings() or {}
    ep = s.get("llm_endpoint") or {}
    st = domains.settings.credentials.service.get_store().key_status(_ENDPOINT_KEY_ENV)
    return {
        "url": ep.get("url") or "",
        "model": ep.get("model") or "auto",
        **asdict(st),  # has_key, source, last4
    }


def _write_endpoint(body: schemas.EndpointBody) -> None:
    store = domains.settings.credentials.service.get_store()
    s = store.read_settings() or {}
    s["llm_endpoint"] = {
        "url": body.url.strip(),
        "model": (body.model or "auto").strip() or "auto",
    }
    store.write_settings(s)
    if body.api_key is not None:
        if body.api_key.strip():
            store.set_key(_ENDPOINT_KEY_ENV, body.api_key.strip())
        else:
            try:
                store.delete_key(_ENDPOINT_KEY_ENV)
            except Exception:
                pass
    domains.settings.chat.service.reset_chat_client()


# ---------------------------------------------------------------------------
# Embedding endpoint — independent connection from the LLM Endpoint above,
# same shape and same flexibility.
# ---------------------------------------------------------------------------

_EMBEDDING_KEY_ENV = "COELHO_EMBEDDING_API_KEY"


def _embedding_view() -> dict:
    s = domains.settings.credentials.service.get_store().read_settings() or {}
    ep = s.get("embedding_endpoint") or {}
    st = domains.settings.credentials.service.get_store().key_status(_EMBEDDING_KEY_ENV)
    return {
        "url": ep.get("url") or "",
        "model": ep.get("model") or "auto",
        **asdict(st),  # has_key, source, last4
    }


def _write_embedding(body: schemas.EmbeddingBody) -> None:
    store = domains.settings.credentials.service.get_store()
    s = store.read_settings() or {}
    s["embedding_endpoint"] = {
        "url": body.url.strip(),
        "model": (body.model or "auto").strip() or "auto",
    }
    store.write_settings(s)
    if body.api_key is not None:
        if body.api_key.strip():
            store.set_key(_EMBEDDING_KEY_ENV, body.api_key.strip())
        else:
            try:
                store.delete_key(_EMBEDDING_KEY_ENV)
            except Exception:
                pass
    domains.settings.embeddings.service.reset_embedding_client()
