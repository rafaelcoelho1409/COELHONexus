"""Celery application — broker, queue routing, and worker process init.

Invoked from outside Python as `celery -A core.celery worker ...` (see
k8s/helm/templates/celery/deployment.yaml). Task modules in `task_include`
are loaded by the worker at boot.
"""
from .service import app


__all__ = ["app"]
