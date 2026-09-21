"""chat errors — module exception classes (subclass builtins so generic
`except TimeoutError` / `except RuntimeError` callers keep working)."""
from __future__ import annotations


class ChatError(RuntimeError):
    """Base for chat-endpoint failures (auth, server 5xx, SDK missing)."""


class ChatTimeoutError(TimeoutError):
    """Hard wall-clock backstop fired — the call exceeded timeout_s + margin."""
