"""doc_distill — pure helpers (JSON parse, fallback distillate, manifest
hash). Prompt builder lives in prompts.py; Pydantic schemas in schemas.py."""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Optional

from .params import (
    FB_STOP,
    KEY_TERM_CHARS_MAX,
    KEY_TERM_CHARS_MIN,
    KEY_TERMS_MAX,
    KEY_TERMS_MIN,
    SUMMARY_WORDS_MAX,
    SUMMARY_WORDS_MIN,
)
from .patterns import FB_IDENT_RE, H1_RE, JSON_RE
from .schemas import DocDistillate
from .versions import PROMPT_VERSION


def parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = JSON_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def try_validate(d: dict) -> tuple[Optional[DocDistillate], Optional[str]]:
    try:
        return DocDistillate.model_validate(d), None
    except Exception as e:
        return None, str(e)[:200]


# Per-doc failure-reason buckets. `rate_limit` and `timeout` are TRANSIENT
# (caller retries); the rest fall through to the deterministic fallback
# immediately. The classifier is intentionally string-matching — provider
# clients raise different exception classes for the same operational
# condition (LiteLLM rate-limit vs httpx 429 vs Google InternalServerError
# with "quota" in the message), so the lowest-common-denominator signal is
# substring presence in the str(exc).
def classify_error(exc: Exception) -> str:
    name = type(exc).__name__
    msg = str(exc).lower()
    if "rate" in msg or "429" in msg or "quota" in msg or "throttle" in msg:
        return "rate_limit"
    if "timeout" in msg or "timeout" in name.lower() or "timed out" in msg:
        return "timeout"
    if "context" in msg and ("length" in msg or "size" in msg or "window" in msg):
        return "context_length"
    if "auth" in msg or "401" in msg or "403" in msg or "permission" in msg:
        return "auth"
    if "connection" in msg or "network" in msg or "refused" in msg:
        return "connection"
    return name or "unknown"


def doc_title(source_key: str, body: str) -> str:
    """First H1 heading, else a filename-derived title."""
    m = H1_RE.search(body or "")
    if m:
        t = m.group(1).strip().strip("#").strip()
        if t:
            return t
    base = source_key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    base = re.sub(r"^\d+-", "", base)
    return " ".join(w.capitalize() for w in base.split("-")) or source_key


def build_fallback_distillate(source_key: str, body: str) -> DocDistillate:
    """Deterministic minimal distillate so content-bearing docs reach
    downstream even when LLM distill fails (silent-drop caused 6-18 doc
    losses in earlier runs). Derived from H1/filename + identifier tokens."""
    title = doc_title(source_key, body)
    words = (
        f"Reference documentation covering {title} and its usage, "
        f"configuration, and related concepts in the framework."
    ).split()
    if len(words) < SUMMARY_WORDS_MIN:
        words += (
            "from the official documentation set describing core concepts "
            "and configuration"
        ).split()
    summary = " ".join(words[:SUMMARY_WORDS_MAX])

    terms: list[str] = []
    seen: set[str] = set()
    for tok in FB_IDENT_RE.findall(body or ""):
        low = tok.lower()
        if low in FB_STOP or low in seen:
            continue
        seen.add(low)
        terms.append(tok[:KEY_TERM_CHARS_MAX])
        if len(terms) >= KEY_TERMS_MAX:
            break
    if len(terms) < KEY_TERMS_MIN:
        for w in title.split():
            if len(w) >= KEY_TERM_CHARS_MIN and w.lower() not in seen:
                seen.add(w.lower())
                terms.append(w[:KEY_TERM_CHARS_MAX])
            if len(terms) >= KEY_TERMS_MIN:
                break
    for g in ("overview", "reference", "guide"):
        if len(terms) >= KEY_TERMS_MIN:
            break
        if g not in seen:
            seen.add(g)
            terms.append(g)
    return DocDistillate(
        summary = summary, key_terms = terms[:KEY_TERMS_MAX],
    )


def manifest_hash(*, slug: str, relevant_files: list[str]) -> str:
    h = sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(relevant_files):
        h.update(b"|")
        h.update(k.encode())
    return h.hexdigest()[:16]
