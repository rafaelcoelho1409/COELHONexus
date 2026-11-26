"""Shared construction helpers for the Book Distiller graph package."""
from __future__ import annotations
import domains

from typing import Any

from langgraph.types import RetryPolicy


def provider_retry_policy() -> RetryPolicy:
    """Build the retry policy shared by provider-backed Distill graph nodes."""
    return RetryPolicy(
        max_attempts = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_ATTEMPTS,
        initial_interval = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S,
        backoff_factor = 2.0,
        max_interval = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_MAX_INTERVAL_S,
        jitter = True,
        retry_on = domains.settings.chat.domain.is_transient_provider_error,
    )


def distill_graph(checkpointer: Any | None = None):
    """Compile the root Distill graph."""
    from .main import graphs

    return graphs.distill_graph(checkpointer = checkpointer)
