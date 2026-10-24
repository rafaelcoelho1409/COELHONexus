"""Build-tab code synthesis — paper-extraction → complete Python, via a
3-round generate/critique/revise loop against the rotator's rr-strong pool."""
from __future__ import annotations
from . import domain, params, prompts, service
