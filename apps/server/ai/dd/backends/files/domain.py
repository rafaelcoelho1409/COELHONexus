"""Pure helpers of the files backend: the path an agent's call reaches after the composite backend strips the route."""
from __future__ import annotations


def relative_path(path: str | None, route: str) -> str:
    """A path as the backend sees it. The composite backend strips the route, but a bare call may not."""
    value = "/" + (path or "").strip().strip("/")
    prefix = "/" + route.strip("/")
    if value == prefix or value.startswith(prefix + "/"):
        value = value[len(prefix):] or "/"
    return value
