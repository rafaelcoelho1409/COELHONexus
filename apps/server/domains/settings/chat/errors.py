"""chat errors — module exception classes (subclass builtins so generic
`except TimeoutError` / `except RuntimeError` callers keep working)."""
from __future__ import annotations


class ChatError(RuntimeError):
    """Base for chat-endpoint failures (auth, server 5xx, SDK missing)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ChatTimeoutError(TimeoutError):
    """Hard wall-clock backstop fired — the call exceeded timeout_s + margin."""


class ChatPoolUnsettledError(RuntimeError):
    """The Rotator stayed above its cooldown threshold for the full settle window."""

    def __init__(self, *, waited_s: float, cooling: int, pool: int) -> None:
        self.waited_s = waited_s
        self.cooling = cooling
        self.pool = pool
        super().__init__(
            f"LLM routing pool remained degraded after {waited_s:.0f}s "
            f"({cooling}/{pool} deployments cooling down)"
        )


class ChatAdmissionTimeoutError(RuntimeError):
    """The shared LLM admission queue stayed full beyond its configured bound."""
