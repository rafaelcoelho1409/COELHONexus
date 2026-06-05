"""Ingestion tiers — re-exports of the per-tier `run()` entry points + the
two control-flow exceptions used to fall through between tiers."""
from __future__ import annotations

from . import tier1, tier2, tier3, tier4, tier5
from .errors import EmptyLinksDetected, ManifestDetected
from .tier1 import run as tier1_run
from .tier2 import run as tier2_run
from .tier3 import run as tier3_run
from .tier4 import run as tier4_run
from .tier5 import run as tier5_run

__all__ = [
    "EmptyLinksDetected",
    "ManifestDetected",
    "tier1",
    "tier1_run",
    "tier2",
    "tier2_run",
    "tier3",
    "tier3_run",
    "tier4",
    "tier4_run",
    "tier5",
    "tier5_run",
]
