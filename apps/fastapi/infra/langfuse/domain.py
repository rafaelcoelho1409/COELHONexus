"""langfuse domain — pure span-attribute encoders (no I/O, deterministic)."""
from __future__ import annotations

import json
from typing import Any

from . import params, patterns


def truncate(text: str, cap: int) -> str:
    if cap <= 0 or len(text) <= cap:
        return text
    return text[:cap] + f"…+{len(text) - cap}b"


def json_attr(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        raw = json.dumps(str(value), ensure_ascii=False)
    return truncate(raw, params.JSON_ATTR_CAP)


def metadata_key(key: str) -> str:
    safe = patterns.SAFE_KEY_RE.sub("_", str(key).strip())
    return safe.strip("._-") or "value"


def metadata_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return truncate(value, params.METADATA_VALUE_CAP)
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        raw = str(value)
    return truncate(raw, params.METADATA_VALUE_CAP)
