"""Shared construction helpers for the Book Distiller graph package."""
from __future__ import annotations
import domains

from typing import Any

from langgraph.types import RetryPolicy


def provider_retry_policy(
    *,
    retry_capacity_errors: bool = True,
    max_attempts: int | None = None,
) -> RetryPolicy:
    """Build the retry policy shared by provider-backed Distill graph nodes."""
    def retry_provider_error(error: Exception) -> bool:
        if not domains.settings.chat.domain.is_transient_provider_error(error):
            return False
        if retry_capacity_errors:
            return True
        return domains.settings.chat.domain.capacity_wait_s(
            error,
            default_s = domains.settings.chat.params.CAPACITY_WAIT_DEFAULT_S,
            min_s = domains.settings.chat.params.CAPACITY_WAIT_MIN_S,
            cap_s = domains.settings.chat.params.CAPACITY_WAIT_CAP_S,
        ) is None

    return RetryPolicy(
        max_attempts = (
            max_attempts
            if max_attempts is not None
            else domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_ATTEMPTS
        ),
        initial_interval = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_INITIAL_INTERVAL_S,
        backoff_factor = 2.0,
        max_interval = domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_MAX_INTERVAL_S,
        jitter = True,
        retry_on = retry_provider_error,
    )


def distill_graph(checkpointer: Any | None = None):
    """Compile the root Distill graph."""
    from .main import graphs

    return graphs.distill_graph(checkpointer = checkpointer)
