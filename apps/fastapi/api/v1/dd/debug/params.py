"""debug router — tunables + lookup tables."""
from __future__ import annotations

from domains.dd.ingestion.tiers import tier1, tier2, tier3, tier4, tier5


TIER_BY_KIND = {
    "llms_full": (1, tier1),
    "llms_txt":  (2, tier2),
    "sitemap":   (3, tier3),
    "docs":      (4, tier4),
    "github":    (5, tier5),
}


KIND_BY_TIER = {n: kind for kind, (n, _) in TIER_BY_KIND.items()}
