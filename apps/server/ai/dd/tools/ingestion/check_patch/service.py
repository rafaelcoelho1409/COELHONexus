"""Checking one patch against the page the agent holds, and what the model reads of the result."""
from __future__ import annotations
from . import domain, entities, params

import hashlib
import json
import typing


def verdict(files: dict[str, typing.Any], check: entities.Check, fields: dict[str, typing.Any]) -> str:
    """Run `check` on the patch `fields` against the page and audit in `files`: OK, or why the patch would be refused and what to change."""
    markdown = domain.file_text(files, params.PAGE_PATH)
    if not markdown:
        return "ERROR: the page is not available to check against; answer with the patches you have."
    assessment = json.loads(domain.file_text(files, params.ASSESSMENT_PATH) or "{}")
    reason = check(markdown, hashlib.sha256(markdown.encode("utf-8")).hexdigest(), assessment, fields)
    if not reason:
        return "OK: this patch would be applied. Keep it in your answer unchanged."
    return f"REJECTED: {reason[:params.REASON_CHARS]}"
