"""Docs Distiller — Ingestion (Step 2).

Public entry point is `dispatch.run(run_id, slug)`. Tiers fan out from
there based on the resolver's `best_source.tier` for the picked framework.
"""
