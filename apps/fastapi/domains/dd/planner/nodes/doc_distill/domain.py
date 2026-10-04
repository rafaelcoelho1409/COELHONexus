"""doc_distill — pure helpers (fallback distillate, manifest hash).
Prompt builder lives in prompts.py; Pydantic schemas in schemas.py.
LLM output decoding is `chat_structured_async` — no manual JSON parse
needed here anymore."""
from __future__ import annotations
from . import params, patterns, schemas, versions

import re
from hashlib import sha256





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


def _clip_summary(summary: str) -> str:
    """Whitespace-collapse; over-long → the longest leading run of whole sentences within SUMMARY_WORDS_MAX words (if it still has ≥ SUMMARY_WORDS_MIN), else a hard clip at the limit."""
    words = " ".join((summary or "").split()).split()
    if len(words) <= params.SUMMARY_WORDS_MAX:
        return " ".join(words)
    head = " ".join(words[:params.SUMMARY_WORDS_MAX])
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", head)]
    if ends and len(head[:ends[-1]].split()) >= params.SUMMARY_WORDS_MIN:
        return head[:ends[-1]]
    return head.rstrip(",;:- ")


def normalize_distillate(
    summary: str, key_terms: list[str], source_key: str, body: str,
) -> schemas.DocDistillate:
    """Normalize-then-validate for the LLM's distillate: clip an over-long
    summary, top up a too-short one and a key-term shortfall from the doc itself
    (identifier tokens, then title words — the same sources the deterministic
    fallback uses), so a near-miss keeps the model's actual content. Output
    always satisfies `DocDistillate`; anything unfixable raises for the caller's
    fallback."""
    title = doc_title(source_key, body)
    clipped = _clip_summary(summary)
    words = clipped.split()
    if len(words) < params.SUMMARY_WORDS_MIN:
        if not words:
            raise ValueError("empty summary")
        words += f"(from {title})".split()
        if len(words) < params.SUMMARY_WORDS_MIN:
            words += "in the official documentation".split()
    clipped = " ".join(words[:params.SUMMARY_WORDS_MAX])

    terms: list[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        t = " ".join((t or "").strip().split())
        if (
            params.KEY_TERM_CHARS_MIN <= len(t) <= params.KEY_TERM_CHARS_MAX
            and t.casefold() not in seen and len(terms) < params.KEY_TERMS_MAX
        ):
            seen.add(t.casefold())
            terms.append(t)

    for t in key_terms or []:
        _add(t)
    if len(terms) < params.KEY_TERMS_MIN:
        for tok in patterns.FB_IDENT_RE.findall(body or ""):
            if tok.lower() not in params.FB_STOP:
                _add(tok)
            if len(terms) >= params.KEY_TERMS_MIN:
                break
    if len(terms) < params.KEY_TERMS_MIN:
        for w in title.split():
            _add(w)
            if len(terms) >= params.KEY_TERMS_MIN:
                break
    for g in ("overview", "reference", "guide"):
        if len(terms) >= params.KEY_TERMS_MIN:
            break
        _add(g)
    return schemas.DocDistillate(summary = clipped, key_terms = terms)


def manifest_hash(*, slug: str, relevant_files: list[str]) -> str:
    h = sha256()
    h.update(versions.PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(relevant_files):
        h.update(b"|")
        h.update(k.encode())
    return h.hexdigest()[:16]
