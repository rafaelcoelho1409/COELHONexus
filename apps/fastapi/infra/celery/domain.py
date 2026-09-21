"""celery domain — pure connection-string builders (no I/O, deterministic).

`params.py` reads the env once and calls these; unit-test these, not the
import-time env reads.
"""
from __future__ import annotations


def build_redis_url(host: str, port: str | int, password: str) -> str:
    """Broker/result-backend URL. Password omitted when blank — a blank
    `: @` segment breaks redis-py DSN parsing."""
    host = (host or "").strip()
    password = (password or "").strip()
    if password:
        return f"redis://:{password}@{host}:{port}"
    return f"redis://{host}:{port}"


def scoped_queue(base: str, environment: str) -> str:
    """`{base}-{environment}` so dev/prod never steal each other's tasks
    off the shared Redis broker.

    Cross-boundary contract: the Helm chart reimplements this exact shape
    independently (`k8s/helm/templates/celery/deployment.yaml` appends
    `-{{ .Values.environment }}` to each of `Values.celery.queues`). Change
    the shape here and there together, or workers will subscribe to queues
    the router never fills (silent task pile-up, no error anywhere)."""
    return f"{base.strip()}-{environment.strip().lower()}"
