"""Validate and render BD Query workflow prompts."""
from __future__ import annotations
from . import prompts

import domains


def _validate_backend(backend: str) -> None:
    if backend not in domains.bd.query.source.params.BACKENDS:
        raise ValueError(f"Unsupported backend: {backend}.")


def build_draft_query_prompt(
    backend: str,
    user_request: str,
    previous_query: str = "",
) -> str:
    _validate_backend(backend)
    user_request = user_request.strip()
    if not user_request or len(user_request) > 4_000:
        raise ValueError("user_request must contain between 1 and 4000 characters")
    if len(previous_query) > domains.bd.query.source.params.MAX_QUERY_BODY_CHARS:
        raise ValueError("previous_query exceeds the BD Query body size limit")
    return prompts.DRAFT_QUERY_TEMPLATE.format(
        backend=backend,
        user_request=user_request,
        previous_query=previous_query.strip() or "(none)",
    )


def build_investigate_prompt(backend: str, user_request: str) -> str:
    _validate_backend(backend)
    user_request = user_request.strip()
    if not user_request or len(user_request) > 4_000:
        raise ValueError("user_request must contain between 1 and 4000 characters")
    return prompts.INVESTIGATE_TEMPLATE.format(
        backend=backend,
        user_request=user_request,
    )
