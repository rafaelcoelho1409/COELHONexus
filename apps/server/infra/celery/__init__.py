"""Celery application — broker, queue routing, and worker process init.

Invoked from outside Python as `celery -A infra.celery.service:app
worker ...` (see k8s/helm/templates/celery/deployment.yaml). Task
modules in `task_include` are loaded by the worker at boot.

Module-only re-exports like every other package — the Celery app
itself is reached dotted (`infra.celery.service.app`), including the
`@infra.celery.service.app.task(...)` decorators in task modules.
"""
from . import domain, keys, params, service


__all__ = ["domain", "keys", "params", "service"]
