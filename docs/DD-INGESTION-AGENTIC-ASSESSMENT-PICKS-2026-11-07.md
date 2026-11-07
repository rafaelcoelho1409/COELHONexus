# DD Ingestion — which docs to ingest and assess for the agentic stage (2026-11-07)

The agentic stage (audit, then repair, then publish) is the ingestion process now. It earns its cost where pages carry real furniture, not on clean `llms-full.txt` bundles: on both Evidently runs it changed 0 page bodies for ~110 audit calls. So the picks below favour Tier 2–4 docs with known residue, small enough to run.

Sizes come from probes run on 2026-11-07 (sitemap `<loc>` counts under each doc's `path_filter`; `objects.inv` `std:doc` counts for Sphinx docs). Nothing was ingested to make this list. What is already in MinIO was not checked.

## The picks (about 640 pages in total)

| Order | Doc | Tier | Pages | What it tells you |
|---|---|---|---|---|
| 1 | **DeepAgents** | 3 (sitemap) | ~73 | First live run of the `check_patch`, tool-allowlist and harness-profile fixes (offline-tested only). Small, so a failure is cheap. |
| 2 | **React** | 2 (`llms.txt`) | ~180 | A known list of leftover defects (JSX wrappers, `<img className>`, `<FullWidth>`, `<CodeDiagram>`) to score the agent against. |
| 3 | **FastAPI** | 3 (sitemap) | 153 | A second site generator (MkDocs Material), code-heavy. Shows whether run 1's results hold beyond the Mintlify site. |
| 4 | **Celery (Python)** | 4 (crawl) | ~234 | Crawled Sphinx pages with autodoc residue, the noisiest class. |

If only two fit: DeepAgents and Celery.

Left out on purpose:
- **Tier 1:** clean bundles come out unchanged (Evidently, Locust). The banner pages below are a deterministic fix.
- **LangGraph, LangChain:** same site and ~37 shared pages (concepts, migrate, releases, integrations) as DeepAgents, so the same noise again.
- **LlamaIndex (448), OpenTelemetry (831), Docker (2,175), Kubernetes:** scale tests, no new failure class.

## What to record on each run

Same set as the Evidently and Locust assessments:
- pages whose body the stage changed, and whether each change was correct;
- patches accepted versus rejected, with the rejection reasons;
- invented duplicate findings;
- tokens and seconds per page;
- behaviour under an upstream outage (the page must keep its audited original).

## Finding: agent banners become a page of their own (Tier 1, offline)

Splitting the live bundles with Tier 1's own `normalize_doc` + `split_monolith` gives a junk page 0:

| Doc | Pages | Page 0 |
|---|---|---|
| Pydantic | 328 (93 stubs dropped) | `pydantic-querying-this-documentation`, 767 B ("agent query parameters … not included") |
| Unsloth | 202 | `unsloth-agent-instructions`, 1,206 B (GitBook "ask interface" block) |

- Unsloth's `llms-full.txt` is paginated into 3 parts (pages 1–100, 101–200, 201). Tier 1 follows the "Next part" links (`MAX_PARTS = 25`) and joins them correctly: 201 real pages plus the banner. A comment in `tiers/tier1/params.py` still says "2 parts".
- Not checked: whether the agentic stage later drops these pages.
- Proposed fix: strip the banner in the normalizer, next to `_agent_banner_pass`. Not applied.

## Full per-tier order (for later)

Cheapest and most useful first. Page counts are given only where probed.

- **Tier 1 (13 docs).** Done: Claude Code, FastMCP, Browser Use (downloads); Evidently, Locust (agentic). Next: Pydantic, Unsloth, TRL (274 pages, versioned autodoc), Streamlit (247, no nav order), SGLang (401). Scale only, last: Dask, dbt, Next.js.
- **Tier 2 (5 docs).** Optimum (16), React (~180), Diffusers (397, ~20 min of Hugging Face rate limit), Qdrant (expected to fall through to Tier 3 on the coverage check), Transformers (771, ~35 min of waiting, a few 138–170 KB pages).
- **Tier 3 (56 docs).** DeepAgents, LangGraph, LangChain, FastAPI, LlamaIndex; then LangFuse, LiteLLM, vLLM, Crawl4AI (not sized); last OpenTelemetry, Kubernetes, Docker.
- **Tier 4 (49 docs).** Celery (234), Sentence Transformers (170, `package_reference` excluded), Optuna (181, `reference` excluded), Pytest (257); last NumPy, Pandas, Scikit-Learn, PySpark. TypeScript, Go, Bash, XGBoost and the DuckDB/Playwright Python docs already have download oracles.
- **Tier 5.** No catalog entry routes to it first.
