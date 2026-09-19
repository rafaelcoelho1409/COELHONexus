"""Ingestion domain — entry: dispatch.service.run(run_id, slug); tiers dispatched by catalog best_source.tier."""
from __future__ import annotations

from . import artifacts, dispatch, filters, observability, post, progress, storage, tiers
