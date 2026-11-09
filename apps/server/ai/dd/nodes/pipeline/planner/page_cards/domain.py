"""Pure Planner v2 card helpers."""
from __future__ import annotations

from hashlib import sha256

from . import patterns


def content_digest(body: str) -> str:
    return sha256((body or "").encode("utf-8")).hexdigest()


def manifest_digest(slug: str, source_keys: list[str], body_digests: dict[str, str], version: str) -> str:
    payload = "\n".join([slug, version, *[f"{key}:{body_digests.get(key, '')}" for key in source_keys]])
    return sha256(payload.encode("utf-8")).hexdigest()[:20]


def fallback_card(*, key: str, title: str, body: str, url: str = "") -> dict:
    """Keep failed-to-card pages visible and give the architect a deterministic hint."""
    if not (body or "").strip():
        return {
            "keep": False, "reason": "The stored page is empty.", "kind": "overview",
            "topic": title or key.rsplit("/", 1)[-1], "summary": "No page content was available.",
            "teaches": [], "code_role": "none", "prerequisites": [], "fallback": True,
        }
    headings = [" ".join(h.split()) for h in patterns.HEADING_RE.findall(body)]
    topic = (headings[0] if headings else title or key.rsplit("/", 1)[-1]).replace("-", " ")
    is_reference = bool(patterns.REFERENCE_PATH_RE.search(url or key))
    has_code = chr(96) * 3 in body or "~~~" in body
    return {
        "keep": True,
        "reason": "Kept because semantic card generation failed; the page is not silently discarded.",
        "kind": "reference" if is_reference else "how_to",
        "topic": " ".join(topic.split()[:8]),
        "summary": " ".join((headings[:2] or [topic])[:2]),
        "teaches": headings[:3],
        "code_role": "runnable_example" if has_code else "none",
        "prerequisites": [],
        "fallback": True,
    }


def normalize_reference_kind(card: dict, *, key: str, title: str, url: str) -> dict:
    """Keep ambiguous feature and configuration docs in the learning path; exclude only clear lookup pages."""
    if card.get("kind") != "reference":
        return card
    locator = " ".join((key or "", title or "", url or "")).casefold()
    teaches = card.get("teaches") or []
    teaches_text = teaches if isinstance(teaches, str) else " ".join(str(value) for value in teaches)
    learning_text = " ".join([
        locator,
        str(card.get("summary") or ""),
        teaches_text,
    ])
    strong_learning_signal = (
        patterns.LEARNING_FILE_RE.search(locator)
        or card.get("code_role") == "runnable_example"
    )
    if strong_learning_signal:
        return {**card, "kind": "how_to"}
    if patterns.REFERENCE_DOC_PATH_RE.search(url or ""):
        return card
    if patterns.API_PATH_RE.search(url or ""):
        return card
    if patterns.TEACHING_LANGUAGE_RE.search(learning_text):
        return {**card, "kind": "how_to"}
    if patterns.LOOKUP_FILE_RE.search(locator):
        return card
    # A short card summary is not enough evidence to remove an ambiguous page from the tutorial.
    return {**card, "kind": "concept"}


def to_distillate(card: dict) -> dict:
    """Compatibility shape for the existing selector's page-family rules."""
    role_by_kind = {
        "concept": "explanation",
        "how_to": "how_to",
        "tutorial": "tutorial",
        "reference": "reference",
        "overview": "overview",
    }
    terms = [card.get("topic") or "", *(card.get("teaches") or []), *(card.get("prerequisites") or [])]
    return {
        "summary": card.get("summary") or card.get("topic") or "",
        "key_terms": list(dict.fromkeys(term for term in terms if term))[:12],
        "role": role_by_kind.get(card.get("kind"), "explanation"),
    }


def card_text(card: dict) -> str:
    return " ".join([
        str(card.get("topic") or ""),
        str(card.get("summary") or ""),
        " ".join(card.get("teaches") or []),
        " ".join(card.get("prerequisites") or []),
    ]).strip()
