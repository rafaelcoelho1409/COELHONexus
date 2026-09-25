"""Full smoke test for the coelhonexus-mcp server — all 5 tools + 2 resources + 1 prompt.

Run AFTER `skaffold dev` is up and the fastmcp port-forward is live:

    uv run python scripts/smoke_all.py
    uv run python scripts/smoke_all.py --url http://localhost:23024/mcp/
    uv run python scripts/smoke_all.py --tools arxiv_search,hn_search  # subset

Or from inside the running pod (no fastmcp install needed on the host):

    kubectl exec -it -n coelhonexus-dev deploy/coelhonexus-fastmcp -- \\
        python scripts/smoke_all.py

Each tool is called ONCE with a small n_max and `raise_on_error=False`, so
one upstream outage (arxiv 406, S2 shared-pool 429) shows as a per-tool
FAIL line instead of aborting the whole run. Exit code is 0 iff every
listing succeeds and every call returns without a transport error —
upstream-empty results (e.g. HF feed empty for the day) still count as OK.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import fastmcp


DEFAULT_URL = "http://localhost:23024/mcp/"

# (tool_name, flat_kwargs) — flat, NOT {"input": {...}} (FastMCP 3.x takes
# the MCP `arguments` object directly; the old test_arxiv_local.py wrapper
# shape was rejected by schema validation).
TOOL_CALLS: tuple[tuple[str, dict], ...] = (
    ("arxiv_search", {"query": "deep agents", "n_max": 3}),
    ("semantic_scholar_search", {"query": "deep agents", "n_max": 3}),
    ("huggingface_daily_papers", {"n_max": 3}),
    ("hn_search", {"query": "agents", "n_max": 3}),
    ("openalex_search", {"query": "deep agents", "n_max": 3}),
)

RESOURCE_URIS: tuple[str, ...] = (
    "radar://latest_digest",
    # concept template needs a concrete name; a miss still exercises the path.
    "radar://concept/deep_agents",
)

PROMPT_NAME = "digest_today"


def _summarize_items(data, limit: int = 3) -> list[str]:
    items = data if isinstance(data, list) else [data]
    lines: list[str] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            title = item.get("title") or item.get("name") or str(item)[:80]
        else:
            title = getattr(item, "title", None) or str(item)[:80]
        lines.append(f"    - {title}")
    return lines


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--tools", default="",
                    help="comma-separated tool subset (default: all 5)")
    args = ap.parse_args()

    wanted = {t.strip() for t in args.tools.split(",") if t.strip()}
    calls = [c for c in TOOL_CALLS if not wanted or c[0] in wanted]
    if wanted:
        unknown = wanted - {c[0] for c in TOOL_CALLS}
        if unknown:
            print(f"unknown tools: {sorted(unknown)}", file=sys.stderr)
            return 2

    failures = 0
    async with fastmcp.Client(args.url) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        print(f"\nTools on {args.url}: {names}")
        for name, _ in calls:
            if name not in names:
                print(f"  [FAIL] {name}: not registered")
                failures += 1

        for name, kwargs in calls:
            if name not in names:
                continue
            try:
                result = await client.call_tool(
                    name, kwargs, raise_on_error=False, timeout=90,
                )
            except Exception as e:
                print(f"  [FAIL] {name}: transport {type(e).__name__}: {e}")
                failures += 1
                continue
            if getattr(result, "is_error", False):
                data = getattr(result, "data", "")
                print(f"  [FAIL] {name}: ToolError {str(data)[:160]}")
                failures += 1
                continue
            data = getattr(result, "data", None)
            n = len(data) if isinstance(data, list) else 1
            print(f"  [ok] {name}: {n} item(s)")
            print("\n".join(_summarize_items(data) or ["    (empty)"]))

        resources = await client.list_resources()
        print(f"\nResources: {[str(r.uri) for r in resources]}")
        for uri in RESOURCE_URIS:
            try:
                parts = await client.read_resource(uri)
                text = getattr(parts[0], "text", "") if parts else ""
                print(f"  [ok] {uri}: {len(text)} chars")
            except Exception as e:
                print(f"  [FAIL] {uri}: {type(e).__name__}: {str(e)[:160]}")
                failures += 1

        prompts = await client.list_prompts()
        print(f"\nPrompts: {[p.name for p in prompts]}")
        try:
            pr = await client.get_prompt(PROMPT_NAME, {"topic": "deep agents"})
            n_msg = len(getattr(pr, "messages", []) or [])
            print(f"  [ok] {PROMPT_NAME}: {n_msg} message(s)")
        except Exception as e:
            print(f"  [FAIL] {PROMPT_NAME}: {type(e).__name__}: {str(e)[:160]}")
            failures += 1

    print(f"\n{'PASS' if failures == 0 else f'{failures} FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
