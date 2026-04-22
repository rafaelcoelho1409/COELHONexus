#!/usr/bin/env python3
"""
Exa Search API — resolver-realistic test harness.

What this tests:
  1. Single-framework queries (baseline recall + relevance)
  2. Crossover burst (15 parallel queries, mimics our worst-case)
  3. GitHub-only projects (the py-spy case)
  4. Post-cutoff frameworks (the DeepAgents case)
  5. Exa's search modes: `auto` (default), `keyword`, `neural`
     — for docs queries, keyword is usually best; neural matters for vaguer intent

Ground truth: we know the canonical docs URL for each framework. A "good" Exa
result has the canonical URL within the top 5 hits. Anything else is noise
that would survive to our LLM rerank stage unnecessarily.

Requires: EXA_API_KEY env var.
Usage: EXA_API_KEY=EXA-... python scripts/test_exa_search.py
"""
import asyncio
import os
import time
import json
from dataclasses import dataclass

import httpx


EXA_API_URL = "https://api.exa.ai/search"
API_KEY = os.environ.get("EXA_API_KEY", "")


# (framework, query, expected_host_substring_in_top_5)
TESTS = [
    ("FastAPI",                      "FastAPI official documentation",                  "fastapi.tiangolo.com"),
    ("LangChain",                    "LangChain official documentation",                "langchain.com"),
    ("NVIDIA GPU Operator",          "NVIDIA GPU Operator official documentation",      "docs.nvidia.com"),
    ("NVIDIA DCGM Exporter",         "NVIDIA DCGM Exporter official documentation",     "github.com/NVIDIA/dcgm-exporter"),
    ("NVIDIA Triton Inference",      "NVIDIA Triton Inference Server docs",             "docs.nvidia.com"),
    ("TensorRT",                     "NVIDIA TensorRT official documentation",          "docs.nvidia.com"),
    ("TensorRT-LLM",                 "NVIDIA TensorRT-LLM documentation",               "nvidia.github.io"),
    ("py-spy",                       "py-spy Python profiler documentation",            "github.com/benfred/py-spy"),
    ("DeepAgents",                   "DeepAgents LangChain framework documentation",    "langchain.com"),
    ("Grafana Alloy",                "Grafana Alloy official documentation",            "grafana.com/docs/alloy"),
    ("Prometheus",                   "Prometheus monitoring official documentation",    "prometheus.io"),
    ("Loki",                         "Grafana Loki LogQL official documentation",       "grafana.com/docs/loki"),
    ("Kubernetes",                   "Kubernetes official documentation",               "kubernetes.io"),
    ("Docker",                       "Docker official documentation",                   "docs.docker.com"),
    ("Pandas",                       "Pandas Python data analysis documentation",       "pandas.pydata.org"),
]


@dataclass
class Hit:
    url: str
    title: str
    score: float


async def exa_search(
    client: httpx.AsyncClient,
    query: str,
    mode: str = "auto",
    num_results: int = 10,
) -> list[Hit]:
    payload = {
        "query": query,
        "numResults": num_results,
        "type": mode,                 # "auto" | "keyword" | "neural"
        "contents": {"text": False},  # we don't need content — URL + title is enough
    }
    r = await client.post(
        EXA_API_URL,
        json = payload,
        headers = {
            "x-api-key": API_KEY,
            "Content-Type": "application/json",
        },
        timeout = 20.0,
    )
    r.raise_for_status()
    data = r.json()
    return [
        Hit(
            url = h.get("url", ""),
            title = (h.get("title") or "").strip(),
            score = h.get("score") or 0.0,
        )
        for h in data.get("results", [])
    ]


def _host_match(url: str, needle: str) -> bool:
    return needle.lower() in url.lower()


async def test_mode(mode: str) -> dict:
    """Run all test queries in `mode`. Returns summary stats."""
    print(f"\n{'='*70}\nExa mode: {mode}\n{'='*70}")
    hits_in_top5 = 0
    no_hits = 0
    total_latency = 0.0
    lines = []

    async with httpx.AsyncClient() as client:
        # Serial — mimics normal resolver load (3 queries per topic).
        for fw, query, canonical in TESTS:
            t0 = time.time()
            try:
                hits = await exa_search(client, query, mode = mode)
            except Exception as e:
                lines.append(f"  ✗ {fw:30s} ERROR {type(e).__name__}: {str(e)[:80]}")
                continue
            elapsed = time.time() - t0
            total_latency += elapsed
            if not hits:
                no_hits += 1
                lines.append(f"  ∅ {fw:30s} 0 hits    [{elapsed:4.1f}s]")
                continue
            top5 = hits[:5]
            matched = any(_host_match(h.url, canonical) for h in top5)
            if matched:
                hits_in_top5 += 1
                marker = "✓"
                pos = next(i + 1 for i, h in enumerate(top5) if _host_match(h.url, canonical))
                lines.append(f"  {marker} {fw:30s} canonical #{pos}/5  [{elapsed:4.1f}s]  {top5[0].url[:60]}")
            else:
                marker = "✗"
                lines.append(f"  {marker} {fw:30s} canonical MISSING  [{elapsed:4.1f}s]  top1: {top5[0].url[:60]}")

    for line in lines:
        print(line)

    n = len(TESTS)
    summary = {
        "mode": mode,
        "canonical_in_top5": hits_in_top5,
        "total": n,
        "recall_at_5": hits_in_top5 / n if n else 0,
        "no_hits": no_hits,
        "avg_latency_s": total_latency / n if n else 0,
    }
    print(f"\n  SUMMARY: {hits_in_top5}/{n} canonical URLs in top 5 "
          f"({100 * hits_in_top5 / n:.0f}% recall@5), "
          f"{no_hits} zero-hit queries, avg {summary['avg_latency_s']:.1f}s/query")
    return summary


async def test_burst() -> None:
    """15 parallel queries — crossover worst-case. Checks rate-limit behavior."""
    print(f"\n{'='*70}\nBurst test: 15 parallel queries\n{'='*70}")
    queries = [q for _, q, _ in TESTS[:15]]
    t0 = time.time()
    async with httpx.AsyncClient() as client:
        tasks = [exa_search(client, q, mode = "auto") for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions = True)
    elapsed = time.time() - t0

    ok = sum(1 for r in results if isinstance(r, list) and r)
    errs = [r for r in results if isinstance(r, Exception)]
    print(f"  15 queries in {elapsed:.1f}s — {ok}/15 returned hits, {len(errs)} errors")
    for e in errs[:3]:
        print(f"    error: {type(e).__name__}: {str(e)[:100]}")


async def main():
    if not API_KEY:
        print("ERROR: set EXA_API_KEY env var")
        return

    summaries = []
    # Test all 3 modes — auto is Exa's default; keyword usually best for docs
    for mode in ("auto", "keyword", "neural"):
        summary = await test_mode(mode)
        summaries.append(summary)
        await asyncio.sleep(2)

    await test_burst()

    print(f"\n{'='*70}\nRESULTS COMPARISON\n{'='*70}")
    print(f"{'Mode':10s}  {'recall@5':10s}  {'zero-hits':10s}  {'avg latency':12s}")
    for s in summaries:
        print(f"{s['mode']:10s}  {s['canonical_in_top5']}/{s['total']}       "
              f"{s['no_hits']}          {s['avg_latency_s']:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
