"""core — cross-cutting infrastructure (config, telemetry, logging, exceptions).

No business logic. Domains and the API layer may import from here; `core`
must never import from `domains/`.
"""
