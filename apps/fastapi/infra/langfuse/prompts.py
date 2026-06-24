"""LangFuse prompt management — versioned, label-deployed prompts with
in-process caching and a bulletproof local fallback.

Pattern (in a planner / synth / agent node):
    from infra.langfuse.prompts import get_prompt

    rendered = get_prompt(
        "dd.planner.chapter_propose",
        label     = "production",
        variables = {"framework": "...", "target_chapters": 7, ...},
        fallback  = _local_build(framework, ...),
    )
    if rendered is None:
        rendered = _local_build(framework, ...)
    return rendered

The local builder remains the source of truth — `get_prompt` is an
override layer. When LangFuse is unavailable, the prompt isn't published,
or any step in fetch+compile fails, `fallback` is returned (or None when
the caller didn't supply one). The pipeline never breaks because of
LangFuse.

Cache stores the PROMPT OBJECT (template + version), keyed by (name, label),
TTL-bounded so live updates show up within `ttl_s` seconds. Substitution
happens per call (variables can change per invocation).
"""
from __future__ import annotations

import logging
import time
from threading import Lock

from .client import get_client


logger = logging.getLogger(__name__)


_DEFAULT_TTL_S = 60
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = Lock()

# Sentinel stored in the cache on 404/fetch-failure so the next call
# within the TTL sees "miss already attempted" and skips the API hit.
_MISS = object()


def get_prompt(
    name: str,
    *,
    label:     str = "production",
    variables: dict | None = None,
    fallback:  str | None = None,
    ttl_s:     int = _DEFAULT_TTL_S,
) -> str | None:
    """Fetch a label-deployed prompt template from LangFuse, compile with
    variables, return the rendered string. Returns `fallback` on any
    failure — caller may pass None to indicate it has its own local
    rendering path."""
    client = get_client()
    if client is None:
        return fallback

    cache_key = f"{name}::{label}"
    now = time.monotonic()
    prompt = None
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > now:
            prompt = cached[1]

    if prompt is _MISS:
        return fallback

    if prompt is None:
        try:
            prompt = client.get_prompt(name, label = label)
        except Exception as e:
            logger.debug(
                f"[langfuse] get_prompt({name!r}, label={label!r}) failed: "
                f"{type(e).__name__}: {e}"
            )
            with _cache_lock:
                _cache[cache_key] = (now + ttl_s, _MISS)
            return fallback
        with _cache_lock:
            _cache[cache_key] = (now + ttl_s, prompt)

    try:
        if variables:
            return prompt.compile(**variables)
        return getattr(prompt, "prompt", None) or fallback
    except Exception as e:
        logger.debug(
            f"[langfuse] compile({name!r}) failed: {type(e).__name__}: {e}"
        )
        return fallback


def invalidate_cache(name: str | None = None) -> None:
    """Drop cached prompts. Useful in tests or when promoting a new label."""
    with _cache_lock:
        if name is None:
            _cache.clear()
        else:
            for k in list(_cache):
                if k.startswith(f"{name}::"):
                    del _cache[k]


def with_langfuse_override(
    prompt_name: str,
    *,
    label: str = "production",
):
    """Decorator that adds a LangFuse-managed-override layer to any prompt
    builder. The local body remains the source of truth; LangFuse is the
    additive layer that wins when a template is published under the given
    name + label.

    Variable substitution uses the decorated function's `kwargs` directly.
    Non-primitive values are coerced via `repr(...)` so complex types
    (lists of dicts, nested dicts) at least produce a deterministic
    string in the rendered prompt.

    Usage (sawc, RR triage, anywhere with a builder fn):
        @with_langfuse_override("dd.synth.sawc.writer")
        def build_writer_prompt(*, framework, chapter_id, ...) -> str:
            return f"... long static body ..."

    When no template is published, this is a no-op (local body runs).
    When a template IS published under `dd.synth.sawc.writer / production`,
    every call uses it with the kwargs substituted in.
    """
    import functools

    def _coerce(v):
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        try:
            return repr(v)
        except Exception:
            return str(v)

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                vars_only = {k: _coerce(v) for k, v in kwargs.items()}
                rendered = get_prompt(
                    prompt_name,
                    label     = label,
                    variables = vars_only,
                )
                if rendered:
                    return rendered
            except Exception:
                pass
            return fn(*args, **kwargs)
        return wrapper
    return deco
