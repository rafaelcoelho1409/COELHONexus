"""Validate and render YCS Query prompts."""
from __future__ import annotations
from . import prompts

import domains


MAX_LIMITS = {"elasticsearch": 200, "qdrant": 200, "neo4j": 100}


def _backend(backend: str) -> tuple[dict, int]:
    query = domains.ycs.query.source
    if backend not in query.params.BACKENDS:
        raise ValueError(f"Unsupported YCS Query backend: {backend}.")
    namespace = query.service.namespaces()["matrix"][query.params.APP_YCS][backend]
    return namespace, MAX_LIMITS[backend]


def build_draft_query_prompt(
    backend: str,
    user_request: str,
    previous_query: str = "",
) -> str:
    namespace, maximum_limit = _backend(backend)
    user_request = user_request.strip()
    if not user_request or len(user_request) > 4_000:
        raise ValueError("user_request must contain between 1 and 4000 characters")
    if len(previous_query) > 100_000:
        raise ValueError("previous_query exceeds the YCS Query body size limit")
    return prompts.DRAFT_QUERY_TEMPLATE.format(
        backend=f"{backend} ({namespace['label']}; {namespace['target']})",
        maximum_limit=maximum_limit,
        user_request=user_request,
        previous_query=previous_query.strip() or "(none)",
    )


def build_investigate_prompt(backend: str, user_request: str) -> str:
    namespace, maximum_limit = _backend(backend)
    user_request = user_request.strip()
    if not user_request or len(user_request) > 4_000:
        raise ValueError("user_request must contain between 1 and 4000 characters")
    return prompts.INVESTIGATE_TEMPLATE.format(
        backend=f"{backend} ({namespace['label']}; {namespace['target']})",
        maximum_limit=maximum_limit,
        user_request=user_request,
    )
