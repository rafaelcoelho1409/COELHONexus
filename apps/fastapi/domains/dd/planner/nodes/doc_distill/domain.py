"""doc_distill — pure helpers (JSON parse, fallback distillate, manifest
hash). Prompt builder lives in prompts.py; Pydantic schemas in schemas.py."""
from __future__ import annotations
from . import params, patterns, schemas, versions

import json
import re
from hashlib import sha256
from typing import Optional



def parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = patterns.JSON_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            # SOTA: json_repair tolerates trailing commas/quotes (x.ai strict still leaks)
            import json_repair  # type: ignore

            return json_repair.loads(m.group(0))  # type: ignore
        except Exception:
            return None


def try_validate(d: dict) -> tuple[Optional[schemas.DocDistillate], Optional[str]]:
    try:
        return schemas.DocDistillate.model_validate(d), None
    except Exception as e:
        return None, str(e)[:200]


# String-match classifier: provider clients raise different exception types for the same condition (LiteLLM vs httpx vs Google); transient = rate_limit/timeout/connection → retry.
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
    m = patterns.H1_RE.search(body or "")
    if m:
        t = m.group(1).strip().strip("#").strip()
        if t:
            return t
    base = source_key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    base = re.sub(r"^\d+-", "", base)
    return " ".join(w.capitalize() for w in base.split("-")) or source_key


def build_fallback_distillate(source_key: str, body: str) -> schemas.DocDistillate:
    """Deterministic minimal distillate so content-bearing docs reach
    downstream even when LLM distill fails (silent-drop caused 6-18 doc
    losses in earlier runs). Derived from H1/filename + identifier tokens."""
    title = doc_title(source_key, body)
    words = (
        f"Reference documentation covering {title} and its usage, "
        f"configuration, and related concepts in the framework."
    ).split()
    if len(words) < params.SUMMARY_WORDS_MIN:
        words += (
            "from the official documentation set describing core concepts "
            "and configuration"
        ).split()
    summary = " ".join(words[:params.SUMMARY_WORDS_MAX])

    terms: list[str] = []
    seen: set[str] = set()
    for tok in patterns.FB_IDENT_RE.findall(body or ""):
        low = tok.lower()
        if low in params.FB_STOP or low in seen:
            continue
        seen.add(low)
        terms.append(tok[:params.KEY_TERM_CHARS_MAX])
        if len(terms) >= params.KEY_TERMS_MAX:
            break
    if len(terms) < params.KEY_TERMS_MIN:
        for w in title.split():
            if len(w) >= params.KEY_TERM_CHARS_MIN and w.lower() not in seen:
                seen.add(w.lower())
                terms.append(w[:params.KEY_TERM_CHARS_MAX])
            if len(terms) >= params.KEY_TERMS_MIN:
                break
    for g in ("overview", "reference", "guide"):
        if len(terms) >= params.KEY_TERMS_MIN:
            break
        if g not in seen:
            seen.add(g)
            terms.append(g)
    return schemas.DocDistillate(
        summary = summary, key_terms = terms[:params.KEY_TERMS_MAX],
    )


def manifest_hash(*, slug: str, relevant_files: list[str]) -> str:
    h = sha256()
    h.update(versions.PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(relevant_files):
        h.update(b"|")
        h.update(k.encode())
    return h.hexdigest()[:16]
