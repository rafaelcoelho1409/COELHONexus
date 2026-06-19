"""LangFuse sessions — group related traces under one user-visible workflow.

A *session* is the LangFuse concept for "all the traces produced by one
logical interaction." Mapping per feature:

  DD    one study run        session_id = study_id
  YCS   one Ask conversation session_id = ask_session_id, user_id = channel_id
  RR    one digest cycle     session_id = digest_id

`session(...)` is a context manager that stamps `session_id` and `user_id`
into OTel baggage so the existing `BaggageSpanProcessor` mirrors them onto
every child span. LangFuse's OTLP ingester reads these baggage-mirrored
attributes and groups the traces automatically.

No LangFuse SDK call is required for the trace path — the OTel pipeline
delivers everything. This module is the same `with session(...)` ergonomics
across all three features so the pattern is recognizable at a glance.
"""
from __future__ import annotations

import contextlib
from typing import Iterator

from infra.otel.baggage import bag_context


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
        "langfuse.session.id": session_id,
    }
    if user_id is not None:
        lf_kwargs["langfuse.user.id"] = user_id
    base_kwargs.update(extra)
    base_kwargs.update(lf_kwargs)
    with bag_context(**base_kwargs):
        yield
