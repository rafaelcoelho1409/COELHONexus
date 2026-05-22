from __future__ import annotations

# Lazy singleton — instruments created on first access after init_otel ran.
_instruments: dict = {}