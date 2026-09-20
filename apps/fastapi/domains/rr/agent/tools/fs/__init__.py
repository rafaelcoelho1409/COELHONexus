"""LangChain @tool wrappers around the module-level virtual fs in
../state.py — used by LLM subagents (deep_read · synthesis · report)
that can only interact with state via tool calls."""
from __future__ import annotations

from . import domain, patterns, service
