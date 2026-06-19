"""Publish RR orchestrator prompts (tools + subagents) to LangFuse.

Reads `ORCHESTRATOR_SYSTEM_PROMPT_TOOLS` and `ORCHESTRATOR_SYSTEM_PROMPT_SUBAGENTS`
from `apps/fastapi/domains/rr/agent/prompts.py` and pushes both with label
`production`. The local constants remain the source of truth — keep them
in sync; re-run this script to publish a new version when the local text
changes.

Run inside the FastAPI container:
    kubectl exec -n coelhonexus-dev <fastapi-pod> -c coelhonexus-fastapi -- \\
        bash -c "PYTHONPATH=/app python /tmp/publish_rr_orchestrator_prompts.py"
"""
from __future__ import annotations

import logging
import sys


logging.basicConfig(level = logging.INFO, format = "%(levelname)s %(name)s: %(message)s")


def main() -> int:
    from infra.langfuse import get_client
    from domains.rr.agent.prompts import (
        ORCHESTRATOR_SYSTEM_PROMPT_SUBAGENTS,
        ORCHESTRATOR_SYSTEM_PROMPT_TOOLS,
    )
    client = get_client()
    if client is None:
        print("[publish-rr] LangFuse client unavailable", file = sys.stderr)
        return 1

    entries = [
        ("rr.agent.orchestrator_tools",     ORCHESTRATOR_SYSTEM_PROMPT_TOOLS),
        ("rr.agent.orchestrator_subagents", ORCHESTRATOR_SYSTEM_PROMPT_SUBAGENTS),
    ]
    n_ok = 0
    for name, body in entries:
        try:
            out = client.create_prompt(
                name   = name,
                prompt = body,
                labels = ["production"],
                type   = "text",
            )
            ver = getattr(out, "version", "?")
            print(f"[publish-rr] pushed {name!r} version={ver}")
            n_ok += 1
        except Exception as e:
            print(
                f"[publish-rr] failed {name!r}: {type(e).__name__}: {e}",
                file = sys.stderr,
            )
    return 0 if n_ok == len(entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
