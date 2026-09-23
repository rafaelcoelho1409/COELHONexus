"""LangFuse I/O shell — every SDK / OTel-mutating entry point, fail-soft.

Owns the imperative side; pure encoders live in `domain.py`, span-attribute
shell in `spans.py`, prompt management in `prompts.py`:

  client     — lazy `Langfuse` singleton (`get_client`, `is_available`)
  sessions   — `session(...)`: stamp session_id + user_id into OTel baggage
               (DD one study run, YCS one Ask conversation, RR one digest)
  scores     — `record_score(...)` onto the active trace
  annotation — `flag_for_review(...)`: span markers + review score
  callbacks  — `build_langchain_callback(...)` for LangChain agents

Every entry point fails soft: missing package, network, or credentials
yield a graceful no-op + a debug log line, never an exception.
"""
from __future__ import annotations
import infra
from . import keys

import contextlib
import logging
import os
import re
import threading
from typing import Iterator, Literal, Sequence


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client — lazy singleton, env-driven.
#
# Env vars (in priority order):
#   LANGFUSE_HOST          base URL (e.g.
#                          http://langfuse-web.langfuse.svc.cluster.local:3000)
#   LANGFUSE_PUBLIC_KEY    project public key (HTTP Basic user)
#   LANGFUSE_SECRET_KEY    project secret key (HTTP Basic password)
#
# If `LANGFUSE_HOST` is unset, falls back to deriving it from
# `LANGFUSE_OTLP_ENDPOINT` by stripping `/api/public/otel*` — one env var
# configures both the OTLP exporter and the SDK.
# ---------------------------------------------------------------------------

_client = None
_init_lock = threading.Lock()
_init_attempted = False


def _resolve_host() -> str | None:
    host = os.environ.get("LANGFUSE_HOST")
    if host:
        return host.rstrip("/")
    otlp = os.environ.get("LANGFUSE_OTLP_ENDPOINT")
    if otlp:
        return re.sub(r"/api/public/otel.*$", "", otlp.rstrip("/"))
    return None


def is_available() -> bool:
    """True iff host + both credentials are present (does not import the SDK)."""
    return bool(
        _resolve_host()
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def get_client():
    """Return the singleton LangFuse client, or None if init failed.

    Idempotent: a failed init is remembered (returns None on every subsequent
    call within the process — no retries) so we don't pay the import + auth
    cost on every hot-path call."""
    global _client, _init_attempted
    if _client is not None:
        return _client
    if _init_attempted:
        return None
    with _init_lock:
        if _client is not None or _init_attempted:
            return _client
        _init_attempted = True
        host = _resolve_host()
        pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sk = os.environ.get("LANGFUSE_SECRET_KEY")
        if not (host and pk and sk):
            logger.info(
                "[langfuse] SDK init skipped — host/public_key/secret_key not "
                "all set (host=%s pk_set=%s sk_set=%s)",
                bool(host), bool(pk), bool(sk),
            )
            return None
        try:
            from langfuse import Langfuse
        except Exception as e:
            logger.warning(
                f"[langfuse] SDK import failed ({type(e).__name__}: {e}) — "
                "SDK features disabled; OTLP trace ingestion still active"
            )
            return None
        try:
            _client = Langfuse(public_key=pk, secret_key=sk, host=host)
            logger.info(f"[langfuse] SDK client initialized → {host}")
            return _client
        except Exception as e:
            logger.warning(
                f"[langfuse] SDK client init failed "
                f"({type(e).__name__}: {e})"
            )
            return None


# ---------------------------------------------------------------------------
# Sessions — group related traces under one user-visible workflow.
#
# `session(...)` stamps `session_id` and `user_id` into OTel baggage so the
# existing `BaggageSpanProcessor` mirrors them onto every child span.
# LangFuse's OTLP ingester reads these baggage-mirrored attributes and
# groups the traces automatically — no SDK call on the trace path.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def session(
    feature: str,
    session_id: str,
    *,
    user_id: str | None = None,
    **extra: str | None,
) -> Iterator[None]:
    """Tag every span inside the block with session_id + (optional) user_id.

    `feature` is folded into baggage as `pipeline` so dashboards can slice
    by feature without needing a separate per-feature span attribute.
    `extra` accepts any other ALLOWED_BAGGAGE_KEYS entry (study_id,
    channel_id, digest_id, framework, arm_name, tenant).

    Stamps BOTH plain (`session_id`) and LangFuse-recognized
    (`langfuse.session.id`) attribute names — LangFuse v3 only promotes
    a trace's session field when the dotted form is present, so the
    plain form alone won't group traces in the UI.
    """
    base_kwargs: dict = {
        "session_id":          session_id,
        "user_id":             user_id,
        "pipeline":            feature,
    }
    lf_kwargs: dict = {
        keys.SESSION_ID: session_id,
    }
    if user_id is not None:
        lf_kwargs[keys.USER_ID] = user_id
    base_kwargs.update(extra)
    base_kwargs.update(lf_kwargs)
    with infra.otel.service.bag_context(**base_kwargs):
        yield


# ---------------------------------------------------------------------------
# Scores — quality signals (grader dims, faithfulness, novelty) on the
# active trace. Fire-and-forget; never consulted for control flow.
#
# Pattern (in a domain metric recorder):
#     import infra
#     infra.langfuse.service.record_score("grader.signal_to_noise", 0.83,
#                                         comment="framework=claude-code")
#
# The score attaches to whichever LangFuse trace the OTel span belongs to —
# the `langfuse_otel` LiteLLM callback already ties OTel trace_id ↔
# LangFuse trace_id, so context-based scoring "just works."
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Annotation — human-review flagging. When a node detects a low-confidence
# outcome (e.g. planner chapter_assign rescue), call
# `flag_for_review("rescued N docs")` inside the node's @traced scope.
# Filter the LangFuse UI by either signal to find traces needing attention.
# ---------------------------------------------------------------------------

Severity = Literal["low", "medium", "high"]


def flag_for_review(
    reason: str,
    *,
    severity: Severity = "low",
    score:    float | None = 1.0,
) -> None:
    """Tag the active trace as needing review. Set span attributes +
    optionally record a `review.required` score."""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        is_recording = getattr(span, "is_recording", None)
        if callable(is_recording) and not is_recording():
            span = None
        if span is not None:
            span.set_attribute(keys.REVIEW_REQUIRED_ATTR, True)
            span.set_attribute(keys.REVIEW_REASON_ATTR,   reason[:240])
            span.set_attribute(keys.REVIEW_SEVERITY_ATTR, severity)
    except Exception as e:
        logger.debug(
            f"[langfuse-annotation] span tag failed: "
            f"{type(e).__name__}: {e}"
        )
    if score is not None:
        try:
            record_score(
                keys.REVIEW_REQUIRED_SCORE, float(score),
                comment = f"{severity}: {reason[:200]}",
            )
        except Exception as e:
            logger.debug(
                f"[langfuse-annotation] score write failed: "
                f"{type(e).__name__}: {e}"
            )


# ---------------------------------------------------------------------------
# Callbacks — LangChain CallbackHandler with fail-soft defaults.
#
# Usage:
#     import infra
#     cb = infra.langfuse.service.build_langchain_callback(
#         session_id=scan_id, user_id=profile_id, tags=["rr", "digest"])
#     callbacks = [c for c in (existing_cb, cb) if c is not None]
#     await agent.ainvoke(..., config={"callbacks": callbacks})
#
# Returns None when LangFuse is unavailable — callers filter Nones out.
# ---------------------------------------------------------------------------

def build_langchain_callback(
    *,
    session_id: str | None = None,
    user_id:    str | None = None,
    tags:       Sequence[str] | None = None,
):
    """Build a LangChain CallbackHandler that emits to LangFuse, or None
    when the SDK / credentials aren't available."""
    if not is_available():
        return None
    try:
        from langfuse.langchain import CallbackHandler
    except Exception as e:
        logger.debug(
            f"[langfuse] CallbackHandler import failed "
            f"({type(e).__name__}: {e}) — agent runs without LangFuse callback"
        )
        return None
    try:
        kwargs: dict = {}
        if session_id:
            kwargs["session_id"] = session_id
        if user_id:
            kwargs["user_id"] = user_id
        if tags:
            kwargs["tags"] = list(tags)
        return CallbackHandler(**kwargs)
    except Exception as e:
        logger.warning(
            f"[langfuse] CallbackHandler init failed: {type(e).__name__}: {e}"
        )
        return None
