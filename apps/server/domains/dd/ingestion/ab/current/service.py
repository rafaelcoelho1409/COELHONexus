"""Arm A: the tier code as it is today, run on a capture."""
from __future__ import annotations
import domains

import asyncio


async def transform(inputs: domains.dd.ingestion.ab.entities.Inputs) -> list[domains.dd.ingestion.transform.entities.Page]:
    """What the tier makes of the captured source. Normalizing and splitting a bundle is seconds of pure Python: in a thread."""
    tier1 = domains.dd.ingestion.tiers.tier1
    if inputs.tier != tier1.params.TIER_KIND:
        raise NotImplementedError(f"arm 'current' is wired for {tier1.params.TIER_KIND} so far, not {inputs.tier}")
    return await asyncio.to_thread(
        tier1.domain.pages_from_bundle, inputs.text, url = inputs.url,
        nav_index = [(url, section) for _title, url, section in inputs.nav_links],
    )
