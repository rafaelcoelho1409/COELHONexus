from __future__ import annotations

import datetime as dt
import time

from .entities import DiscoveryRecord, FreeFilter
from .keys import _GEMINI_FREE_NAME_PREFIXES


# --------------------------------------------------------------------------- #
# Free-tier filter predicates
# --------------------------------------------------------------------------- #
def _filter_all(_m: dict) -> bool:
    return True


def _filter_mistral(m: dict) -> bool:
    """Drop models whose deprecation date is in the past."""
    dep = m.get("deprecation") or m.get("deprecation_date")
    if not dep:
        return True
    try:
        deadline = dt.datetime.fromisoformat(str(dep).replace("Z", "+00:00"))
        return deadline.timestamp() > time.time()
    except Exception:
        return True


def _filter_gemini(m: dict) -> bool:
    name = (m.get("name") or "").strip()
    return name.startswith(_GEMINI_FREE_NAME_PREFIXES)


def _filter_sambanova_pricing(m: dict) -> bool:
    """pricing.prompt == 0 AND pricing.completion == 0 → truly free."""
    pricing = m.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1)) == 0.0 and \
               float(pricing.get("completion", 1)) == 0.0
    except (TypeError, ValueError):
        return False


def _filter_always_false(_m: dict) -> bool:
    """For providers held disabled (paywalled, etc)."""
    return False


# FreeFilter enum value → predicate.
FILTER_DISPATCH = {
    FreeFilter.ALL:               _filter_all,
    FreeFilter.MISTRAL:           _filter_mistral,
    FreeFilter.GEMINI:            _filter_gemini,
    FreeFilter.SAMBANOVA_PRICING: _filter_sambanova_pricing,
    FreeFilter.ALWAYS_FALSE:      _filter_always_false,
}


# --------------------------------------------------------------------------- #
# Response shape normalizers
# --------------------------------------------------------------------------- #
def normalize_response(shape: str, body: dict) -> list[dict]:
    """Provider response → list of model dicts."""
    if shape == "gemini":
        return list(body.get("models") or [])
    return list(body.get("data") or [])


def model_id(provider: str, raw: dict) -> str:
    """Canonical model id from a provider response item."""
    if provider == "gemini":
        return (raw.get("name") or "").removeprefix("models/")
    return str(raw.get("id") or raw.get("name") or "")


# --------------------------------------------------------------------------- #
# Aggregator
# --------------------------------------------------------------------------- #
def flat_alive_list(by_provider: dict[str, list[DiscoveryRecord]]) -> list[DiscoveryRecord]:
    """Flatten {provider: [records]} → single list (rotator-builder convenience)."""
    out: list[DiscoveryRecord] = []
    for records in by_provider.values():
        out.extend(records)
    return out
