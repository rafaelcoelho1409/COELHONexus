"""debug router — tunables + lookup tables."""
from __future__ import annotations
import domains


TIER_BY_KIND = {
    "llms_full": (1, domains.dd.ingestion.tiers.tier1),
    "llms_txt":  (2, domains.dd.ingestion.tiers.tier2),
    "sitemap":   (3, domains.dd.ingestion.tiers.tier3),
    "docs":      (4, domains.dd.ingestion.tiers.tier4),
    "github":    (5, domains.dd.ingestion.tiers.tier5),
}


KIND_BY_TIER = {n: kind for kind, (n, _) in TIER_BY_KIND.items()}
