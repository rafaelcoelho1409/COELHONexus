"""LangFuse scores — attach quality signals (grader dims, faithfulness,
code density, novelty) to whatever trace is in the active OTel context.

Fail-soft: when LangFuse is unavailable, no active trace exists, or the
score write errors out, the call returns silently. Score writes are
fire-and-forget; the pipeline must never depend on them for control flow.

Pattern (in a domain metric recorder):
    from infra.langfuse.scores import record_score
    record_score("grader.signal_to_noise", 0.83,
                 comment="framework=claude-code")

The score attaches to whichever LangFuse trace this OTel span belongs to —
the `langfuse_otel` LiteLLM callback already ties OTel trace_id ↔
LangFuse trace_id, so context-based scoring "just works."
"""
from __future__ import annotations

import logging

from .client import get_client


logger = logging.getLogger(__name__)


def record_score(
    name: str,
    value: float | int | str | bool,
    *,
    comment:        str | None = None,
    trace_id:       str | None = None,
    observation_id: str | None = None,
) -> None:
    """Attach a score to the active trace (or to `trace_id` if provided)."""
    client = get_client()
    if client is None:
        return
    try:
        if trace_id is not None or observation_id is not None:
            kwargs: dict = {"name": name, "value": value}
            if comment is not None:
                kwargs["comment"] = comment
            if trace_id is not None:
                kwargs["trace_id"] = trace_id
            if observation_id is not None:
                kwargs["observation_id"] = observation_id
            client.create_score(**kwargs)
        else:
            kwargs = {"name": name, "value": value}
            if comment is not None:
                kwargs["comment"] = comment
            scorer = (
                getattr(client, "score_current_trace", None)
                or getattr(client, "score_current_observation", None)
                or getattr(client, "score", None)
            )
            if scorer is None:
                logger.debug(
                    "[langfuse] no score_current_* method on client "
                    "— score dropped"
                )
                return
            scorer(**kwargs)
    except Exception as e:
        logger.debug(
            f"[langfuse] record_score({name!r}={value!r}) failed: "
            f"{type(e).__name__}: {e}"
        )
