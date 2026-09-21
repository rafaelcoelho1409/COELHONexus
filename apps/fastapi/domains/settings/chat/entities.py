"""chat entities — value objects (no behavior, see domain.py/service.py)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class EndpointConfig:
    """Resolved chat endpoint. URL/model/key always change together
    (one Settings-page save), so they travel as one value (§3)."""
    base_url: str
    model:    str
    api_key:  str
