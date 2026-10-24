"""celery domain — pure name builders (no I/O, deterministic)."""
from __future__ import annotations


def scoped_queue(base: str, environment: str) -> str:
    """`{base}-{environment}` so dev/prod never steal each other's tasks
    off the shared Redis broker.

    Cross-boundary contract: the Helm chart reimplements this exact shape
    independently (`k8s/helm/templates/celery/deployment.yaml` appends
    `-{{ .Values.environment }}` to each of `Values.celery.queues`). Change
    the shape here and there together, or workers will subscribe to queues
    the router never fills (silent task pile-up, no error anywhere)."""
    return f"{base.strip()}-{environment.strip().lower()}"
