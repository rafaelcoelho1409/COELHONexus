#!/usr/bin/env python3
"""
Test all 3 search API keys + the fallback chain end-to-end.

Runs inside the fastapi pod so it picks up EXA_API_KEY / TAVILY_API_KEY /
JINA_API_KEY from the secret mount.

Usage:
  kubectl cp scripts/test_search_chain.py coelhonexus-dev/<pod>:/tmp/ \
      -c coelhonexus-fastapi-container
  kubectl exec -n coelhonexus-dev <pod> -c coelhonexus-fastapi-container \
      -- /app/.venv/bin/python /tmp/test_search_chain.py

What it tests:
  1. Env-var presence for each key
  2. Each provider individually (hits a known-good docs query)
  3. The full fallback chain (build_search_fallback_chain + search_candidates)
  4. In-process cooldown behavior (simulate a 429 on the primary)
"""
import asyncio
import os
import sys
import time
import traceback

sys.path.insert(0, "/app")

from services.search_chain import (
    ExaProvider,
    TavilyProvider,
    JinaProvider,
    ProviderError,
    build_search_fallback_chain,
    search_candidates,
)


QUERY = '"FastAPI" official documentation'
CROSSOVER_QUERIES = [
    ("FastAPI",       None,   None),
    ("LangChain",     None,   None),
    ("py-spy",        None,   None),
    ("DeepAgents",    None,   None),
    ("Grafana Alloy", None,   None),
]


def _mask(key: str) -> str:
    if not key:
        return "(empty)"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


async def test_individual_providers():
    print("\n" + "=" * 70)
    print("1. INDIVIDUAL PROVIDERS")
    print("=" * 70)

    keys = {
        "exa":     os.environ.get("EXA_API_KEY", ""),
        "tavily":  os.environ.get("TAVILY_API_KEY", ""),
        "jina":    os.environ.get("JINA_API_KEY", ""),
    }
    for name, key in keys.items():
        status = "✓ SET" if key else "✗ MISSING"
        print(f"  {name:8s} {status}  {_mask(key)}")

    providers = [
        ("exa",    ExaProvider(keys["exa"])       if keys["exa"]    else None),
        ("tavily", TavilyProvider(keys["tavily"]) if keys["tavily"] else None),
        ("jina",   JinaProvider(keys["jina"])     if keys["jina"]   else None),
    ]
    print()
    for name, p in providers:
        if p is None:
            print(f"  --- {name}: SKIPPED (no key) ---")
            continue
        print(f"  --- {name}: querying {QUERY!r} ---")
        t0 = time.time()
        try:
            hits = await p.asearch(QUERY, num_results = 5)
        except ProviderError as e:
            print(f"    ✗ {type(e).__name__}: {e}")
            continue
        except Exception as e:
            print(f"    ✗ UNEXPECTED {type(e).__name__}: {e}")
            traceback.print_exc()
            continue
        elapsed = time.time() - t0
        print(f"    ✓ {len(hits)} hits in {elapsed:.2f}s")
        for i, h in enumerate(hits[:3], 1):
            print(f"       {i}. {h.url[:80]}")
            print(f"          {(h.title or '')[:80]}")


async def test_fallback_chain():
    print("\n" + "=" * 70)
    print("2. FALLBACK CHAIN (build_search_fallback_chain + search_candidates)")
    print("=" * 70)
    try:
        chain = build_search_fallback_chain()
    except RuntimeError as e:
        print(f"  ✗ build_search_fallback_chain failed: {e}")
        return
    providers = [p.name for p in chain.providers]
    print(f"  providers: {providers}")

    for fw, aliases, version in CROSSOVER_QUERIES:
        t0 = time.time()
        try:
            hits = await search_candidates(
                chain, fw, aliases = aliases or [], version = version,
            )
        except Exception as e:
            print(f"  ✗ {fw:20s} UNEXPECTED {type(e).__name__}: {e}")
            continue
        elapsed = time.time() - t0
        # Show which provider served each hit (via engine field)
        by_provider = {}
        for h in hits:
            by_provider[h.engine] = by_provider.get(h.engine, 0) + 1
        print(f"  ✓ {fw:20s} {len(hits):2d} hits in {elapsed:4.1f}s  from {dict(by_provider)}")
        for h in hits[:2]:
            print(f"       - {h.url[:75]}  [{h.engine}]")


async def test_cooldown_behavior():
    print("\n" + "=" * 70)
    print("3. COOLDOWN BEHAVIOR (simulated)")
    print("=" * 70)
    try:
        chain = build_search_fallback_chain()
    except RuntimeError as e:
        print(f"  ✗ chain unavailable: {e}")
        return

    # Manually trip a cooldown on the first provider
    if not chain.providers:
        return
    primary = chain.providers[0].name
    chain._cooldown_until[primary] = time.monotonic() + 10.0
    print(f"  Forced {primary} into 10s cooldown")
    print(f"  Querying {QUERY!r} — should cascade to next provider")

    hits = await chain.asearch(QUERY, num_results = 5)
    print(f"  ✓ {len(hits)} hits from {hits[0].engine if hits else '(none)'} "
          f"(expected not={primary})")


async def main():
    await test_individual_providers()
    await test_fallback_chain()
    await test_cooldown_behavior()


if __name__ == "__main__":
    asyncio.run(main())
