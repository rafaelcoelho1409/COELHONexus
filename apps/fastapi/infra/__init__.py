"""infra — cross-cutting infrastructure (OTel/LangFuse telemetry, Celery).

No business logic. Domains and the API layer import from here; `infra`
never imports from `domains/`.
"""
