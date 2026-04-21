#!/usr/bin/env python3
"""
Probe /llms-full.txt, /llms.txt, /sitemap.xml coverage across a framework list.

Why this exists:
  A 200 status is NOT enough — SPA docs sites (Next.js etc.) return HTTP 200
  for arbitrary paths with the SPA shell HTML ("Not Found" rendered client-
  side). Reference case: reference.langchain.com/python/deepagents/llms-full.txt
  returns 200 + HTML, but it's not a real llms-full.txt file.

  This script validates CONTENT, not just status.

Usage (inside the coelhonexus-fastapi-container pod):
  kubectl exec -n coelhonexus coelhonexus-fastapi-XXXX \\
      -c coelhonexus-fastapi-container -- \\
      python /app/scripts/probe_llmstxt_coverage.py

  Or with kubectl cp the file first if /app doesn't have scripts dir:
      kubectl cp scripts/probe_llmstxt_coverage.py \\
          coelhonexus/<pod>:/tmp/probe.py -c coelhonexus-fastapi-container
      kubectl exec -n coelhonexus <pod> -c coelhonexus-fastapi-container \\
          -- python /tmp/probe.py

Output:
  /tmp/llmstxt_coverage.json   — structured per-framework results
  /tmp/llmstxt_coverage.csv    — flat CSV for spreadsheet inspection
  stdout                       — human-readable summary table

Design decisions:
  - httpx.AsyncClient + asyncio.gather for parallelism (NOT one request at a time)
  - Semaphore(30) caps concurrent requests — avoids IP-ban / rate-limit
  - Follow redirects (many docs sites 301 → www or https)
  - 15s timeout per request
  - Content validation (not just status) — distinguishes real txt files from SPA shells
  - Graceful failure: network timeout → UNKNOWN status, not crash
"""
import asyncio
import csv
import json
import sys
from dataclasses import dataclass, asdict
from typing import Literal

import httpx


# =============================================================================
# Framework list — (name, docs_url)
# =============================================================================
# Rules for choosing docs_url:
#   - Use the CANONICAL docs root, NOT the marketing homepage
#   - E.g., LangChain Python: python.langchain.com (NOT langchain.com)
#   - If docs live under /docs path, include it (e.g., docs.anthropic.com)
#   - Tests these paths relative to docs_url:
#       <docs_url>/llms-full.txt
#       <docs_url>/llms.txt
#       <docs_url>/sitemap.xml
FRAMEWORKS: list[tuple[str, str]] = [
    # --- Professional site skills ---
    ("Python",            "https://docs.python.org"),
    ("NumPy",             "https://numpy.org"),
    ("Pandas",            "https://pandas.pydata.org"),
    ("DuckDB",            "https://duckdb.org"),
    ("Plotly",            "https://plotly.com/python"),
    ("Scikit-Learn",      "https://scikit-learn.org"),
    ("River",             "https://riverml.xyz"),
    ("XGBoost",           "https://xgboost.readthedocs.io"),
    ("CatBoost",          "https://catboost.ai"),
    ("YellowBrick",       "https://www.scikit-yb.org"),
    ("UMAP",              "https://umap-learn.readthedocs.io"),
    ("ADTK",              "https://adtk.readthedocs.io"),
    ("TensorFlow",        "https://www.tensorflow.org"),
    ("PyTorch",           "https://pytorch.org"),
    ("Keras",             "https://keras.io"),
    ("OpenAI",            "https://platform.openai.com"),
    ("HuggingFace",       "https://huggingface.co"),
    ("Ollama",            "https://ollama.com"),
    ("Groq",              "https://console.groq.com"),
    ("LangChain",         "https://python.langchain.com"),
    ("LangGraph",         "https://langchain-ai.github.io/langgraph"),
    ("Qdrant",            "https://qdrant.tech"),
    ("OpenCV",            "https://docs.opencv.org"),
    ("Ultralytics",       "https://docs.ultralytics.com"),
    ("MediaPipe",         "https://ai.google.dev/edge/mediapipe"),
    ("Roboflow",          "https://docs.roboflow.com"),
    ("OpenVINO",          "https://docs.openvino.ai"),
    ("Docker",            "https://docs.docker.com"),
    ("Kubernetes",        "https://kubernetes.io"),
    ("Helm",              "https://helm.sh"),
    ("Terraform",         "https://developer.hashicorp.com/terraform"),
    ("ArgoCD",            "https://argo-cd.readthedocs.io"),
    ("K3D",               "https://k3d.io"),
    ("Skaffold",          "https://skaffold.dev"),
    ("MLflow",            "https://mlflow.org"),
    ("FastAPI",           "https://fastapi.tiangolo.com"),
    ("Pydantic",          "https://docs.pydantic.dev"),
    ("Prometheus",        "https://prometheus.io"),
    ("Grafana",           "https://grafana.com/docs"),
    ("PySpark",           "https://spark.apache.org/docs/latest/api/python"),
    ("Delta Lake",        "https://docs.delta.io"),
    ("Streamlit",         "https://docs.streamlit.io"),
    ("PyQt6",             "https://doc.qt.io/qtforpython-6"),
    ("Selenium",          "https://www.selenium.dev"),
    ("PyAutoGUI",         "https://pyautogui.readthedocs.io"),
    ("Apache Airflow",    "https://airflow.apache.org"),
    ("Luigi",             "https://luigi.readthedocs.io"),
    ("MySQL",             "https://dev.mysql.com/doc"),
    ("PostgreSQL",        "https://www.postgresql.org/docs"),
    ("NMAP",              "https://nmap.org"),
    ("ProjectDiscovery",  "https://docs.projectdiscovery.io"),
    ("Shodan",            "https://developer.shodan.io"),
    ("QEMU",              "https://www.qemu.org"),
    # --- Obsidian list (deduped) ---
    ("DeepAgents",        "https://reference.langchain.com/python/deepagents"),
    ("FastMCP",           "https://gofastmcp.com"),
    ("Elasticsearch",     "https://www.elastic.co/docs"),
    ("A2A",               "https://a2aproject.github.io/A2A"),
    ("OpenTelemetry",     "https://opentelemetry.io/docs"),
    ("LangFuse",          "https://langfuse.com/docs"),
    ("Redis",             "https://redis.io/docs"),
    ("Celery",            "https://docs.celeryq.dev"),
    ("Claude Code",       "https://docs.claude.com/en/docs/claude-code"),
    ("Sentence Transformers", "https://sbert.net"),
    ("FastEmbed",         "https://qdrant.github.io/fastembed"),
    ("Playwright",        "https://playwright.dev/python"),
    ("Browser Use",       "https://docs.browser-use.com"),
    ("Crawl4AI",          "https://docs.crawl4ai.com"),
    ("Neo4j",             "https://neo4j.com/docs"),
    ("py-spy",            "https://github.com/benfred/py-spy"),
    ("NVIDIA GPU Operator", "https://docs.nvidia.com/datacenter/cloud-native/gpu-operator"),
    ("NVIDIA DCGM Exporter", "https://github.com/NVIDIA/dcgm-exporter"),
    ("TensorRT",          "https://docs.nvidia.com/deeplearning/tensorrt"),
    ("TensorRT-LLM",      "https://nvidia.github.io/TensorRT-LLM"),
    ("NVIDIA Triton",     "https://docs.nvidia.com/deeplearning/triton-inference-server"),
    ("vLLM",              "https://docs.vllm.ai"),
    ("SGLang",            "https://docs.sglang.ai"),
    ("TRL",               "https://huggingface.co/docs/trl"),
    ("Optimum",           "https://huggingface.co/docs/optimum"),
    ("Kubeflow",          "https://www.kubeflow.org/docs"),
    ("CLIP",              "https://github.com/openai/CLIP"),
    ("Whisper",           "https://github.com/openai/whisper"),
    ("dbt",               "https://docs.getdbt.com"),
    ("Seaborn",           "https://seaborn.pydata.org"),
    ("Sweetviz",          "https://github.com/fbdesignpro/sweetviz"),
    ("Missingno",         "https://github.com/ResidentMario/missingno"),
    ("NVIDIA Merlin",     "https://nvidia-merlin.github.io/Merlin"),
    ("RecBole",           "https://recbole.io"),
    ("TerraTorch",        "https://ibm.github.io/terratorch"),
    ("NetworkX",          "https://networkx.org/documentation/stable"),
    ("Dask",              "https://docs.dask.org"),
    ("Ray",               "https://docs.ray.io"),
    ("Optuna",            "https://optuna.readthedocs.io"),
    ("Skforecast",        "https://skforecast.org"),
    ("Statsmodels",       "https://www.statsmodels.org"),
    ("Nixtla",            "https://nixtlaverse.nixtla.io"),
    ("Shap",              "https://shap.readthedocs.io"),
    ("Numba",             "https://numba.readthedocs.io"),
    ("Scikit-Survival",   "https://scikit-survival.readthedocs.io"),
    ("NeuralProphet",     "https://neuralprophet.com"),
    ("VectorBT",          "https://vectorbt.dev"),
    ("Evidently",         "https://docs.evidentlyai.com"),
    ("Alibi Explain",     "https://docs.seldon.io/projects/alibi"),
    ("Dagster",           "https://docs.dagster.io"),
    ("LaTeX",             "https://www.latex-project.org"),
    ("Qiskit",            "https://docs.quantum.ibm.com"),
    ("Sentry",            "https://docs.sentry.io"),
    ("Terragrunt",        "https://terragrunt.gruntwork.io"),
    ("Novu",              "https://docs.novu.co"),
]


# =============================================================================
# Validation rules
# =============================================================================
ProbeResult = Literal["VALID", "SPA_FAKE", "MISSING", "ERROR"]

# Markers that indicate the response body is HTML (not the expected plain text)
_HTML_MARKERS = ("<html", "<!doctype", "<!DOCTYPE", "<HTML")

# Markers of a valid llms-full.txt: markdown headings + reasonable size
_LLMS_FULL_MIN_SIZE = 500  # bytes — smaller than this = likely stub or error
_MARKDOWN_HEADING = "# "   # h1 or higher


def _validate_llms_full(status: int, body: str, ctype: str) -> tuple[ProbeResult, str]:
    """Returns (result, reason)."""
    if status == 404:
        return "MISSING", "404"
    if status >= 400:
        return "MISSING", f"HTTP {status}"
    if status != 200:
        return "MISSING", f"HTTP {status}"
    if any(m in body[:500] for m in _HTML_MARKERS):
        return "SPA_FAKE", "body is HTML (SPA shell)"
    if len(body) < _LLMS_FULL_MIN_SIZE:
        return "SPA_FAKE", f"body too short ({len(body)} bytes)"
    if _MARKDOWN_HEADING not in body[:2000]:
        return "SPA_FAKE", "no markdown heading in first 2KB"
    return "VALID", f"{len(body)} bytes"


def _validate_llms_txt(status: int, body: str, ctype: str) -> tuple[ProbeResult, str]:
    """llms.txt is usually smaller (index-style) — more lenient size check."""
    if status == 404:
        return "MISSING", "404"
    if status >= 400:
        return "MISSING", f"HTTP {status}"
    if status != 200:
        return "MISSING", f"HTTP {status}"
    if any(m in body[:500] for m in _HTML_MARKERS):
        return "SPA_FAKE", "body is HTML (SPA shell)"
    if len(body) < 50:
        return "SPA_FAKE", f"body too short ({len(body)} bytes)"
    # llms.txt should have either markdown headings OR .md URL references
    has_heading = "#" in body[:1000]
    has_md_url = ".md" in body[:4000]
    if not (has_heading or has_md_url):
        return "SPA_FAKE", "no markdown/md-url markers"
    return "VALID", f"{len(body)} bytes"


def _validate_sitemap(status: int, body: str, ctype: str) -> tuple[ProbeResult, str]:
    """sitemap.xml: must be XML with <loc> entries."""
    if status == 404:
        return "MISSING", "404"
    if status >= 400:
        return "MISSING", f"HTTP {status}"
    if status != 200:
        return "MISSING", f"HTTP {status}"
    head = body[:500].lstrip()
    if not (head.startswith("<?xml") or head.startswith("<urlset") or head.startswith("<sitemapindex")):
        return "SPA_FAKE", "body not XML"
    if "<loc>" not in body[:4000]:
        return "SPA_FAKE", "no <loc> entries found"
    return "VALID", f"{body.count('<loc>')} <loc> entries"


# =============================================================================
# Probe engine
# =============================================================================
@dataclass
class FrameworkProbe:
    name: str
    docs_url: str
    llms_full: ProbeResult
    llms_full_reason: str
    llms_txt: ProbeResult
    llms_txt_reason: str
    sitemap: ProbeResult
    sitemap_reason: str


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore) -> tuple[int, str, str]:
    """Fetch a URL with bounded concurrency. Returns (status, body, content_type)."""
    async with sem:
        try:
            resp = await client.get(url)
            ctype = resp.headers.get("content-type", "")
            # Read at most 100KB — enough to validate the file shape
            body = resp.text[:100_000]
            return resp.status_code, body, ctype
        except httpx.TimeoutException:
            return -1, "", "timeout"
        except httpx.ConnectError as e:
            return -2, "", f"connect_error: {e}"
        except Exception as e:
            return -3, "", f"error: {type(e).__name__}: {e}"


async def _probe_framework(
    client: httpx.AsyncClient,
    name: str,
    docs_url: str,
    sem: asyncio.Semaphore) -> FrameworkProbe:
    """Run all 3 probes for one framework in parallel."""
    base = docs_url.rstrip("/")
    url_full = f"{base}/llms-full.txt"
    url_idx = f"{base}/llms.txt"
    url_map = f"{base}/sitemap.xml"
    results = await asyncio.gather(
        _fetch(client, url_full, sem),
        _fetch(client, url_idx, sem),
        _fetch(client, url_map, sem),
    )
    (s_full, b_full, c_full) = results[0]
    (s_idx, b_idx, c_idx) = results[1]
    (s_map, b_map, c_map) = results[2]
    if s_full < 0:
        r_full, why_full = "ERROR", b_full or c_full or "network error"
    else:
        r_full, why_full = _validate_llms_full(s_full, b_full, c_full)
    if s_idx < 0:
        r_idx, why_idx = "ERROR", b_idx or c_idx or "network error"
    else:
        r_idx, why_idx = _validate_llms_txt(s_idx, b_idx, c_idx)
    if s_map < 0:
        r_map, why_map = "ERROR", b_map or c_map or "network error"
    else:
        r_map, why_map = _validate_sitemap(s_map, b_map, c_map)
    return FrameworkProbe(
        name = name,
        docs_url = docs_url,
        llms_full = r_full,
        llms_full_reason = why_full,
        llms_txt = r_idx,
        llms_txt_reason = why_idx,
        sitemap = r_map,
        sitemap_reason = why_map,
    )


# =============================================================================
# Runner
# =============================================================================
async def _run_all(
    frameworks: list[tuple[str, str]],
    concurrency: int = 30,
    timeout_s: int = 15) -> list[FrameworkProbe]:
    sem = asyncio.Semaphore(concurrency)
    headers = {
        "User-Agent": "COELHONexus-KD-Probe/1.0 (+https://rafaelcoelho1409.github.io)",
        "Accept": "text/plain, text/markdown, application/xml, text/xml, */*",
    }
    timeout = httpx.Timeout(timeout=timeout_s, connect=10.0)
    async with httpx.AsyncClient(
        follow_redirects = True,
        timeout = timeout,
        headers = headers,
        http2 = False,  # some hosts don't announce h2; keep http1 for compat
    ) as client:
        total = len(frameworks)
        async def _progress_wrapper(idx: int, name: str, url: str) -> FrameworkProbe:
            result = await _probe_framework(client, name, url, sem)
            print(
                f"  [{idx:3d}/{total}] {name:28s} "
                f"full={result.llms_full:9s}  "
                f"idx={result.llms_txt:9s}  "
                f"map={result.sitemap:9s}",
                flush = True,
            )
            return result
        tasks = [
            _progress_wrapper(i + 1, name, url)
            for i, (name, url) in enumerate(frameworks)
        ]
        return await asyncio.gather(*tasks)


def _write_outputs(probes: list[FrameworkProbe]) -> None:
    # JSON
    with open("/tmp/llmstxt_coverage.json", "w") as f:
        json.dump([asdict(p) for p in probes], f, indent = 2)
    # CSV
    with open("/tmp/llmstxt_coverage.csv", "w", newline = "") as f:
        w = csv.writer(f)
        w.writerow([
            "framework", "docs_url",
            "llms_full", "llms_full_reason",
            "llms_txt",  "llms_txt_reason",
            "sitemap",   "sitemap_reason",
        ])
        for p in probes:
            w.writerow([
                p.name, p.docs_url,
                p.llms_full, p.llms_full_reason,
                p.llms_txt,  p.llms_txt_reason,
                p.sitemap,   p.sitemap_reason,
            ])
    print("\nWrote /tmp/llmstxt_coverage.json + /tmp/llmstxt_coverage.csv")


def _print_summary(probes: list[FrameworkProbe]) -> None:
    total = len(probes)
    def count(attr: str, val: str) -> int:
        return sum(1 for p in probes if getattr(p, attr) == val)
    print("\n" + "=" * 70)
    print(f"SUMMARY — {total} frameworks tested")
    print("=" * 70)
    print(f"{'File':<20} {'VALID':>8} {'SPA_FAKE':>10} {'MISSING':>10} {'ERROR':>8}")
    for attr, label in [
        ("llms_full", "/llms-full.txt"),
        ("llms_txt",  "/llms.txt"),
        ("sitemap",   "/sitemap.xml"),
    ]:
        v = count(attr, "VALID")
        s = count(attr, "SPA_FAKE")
        m = count(attr, "MISSING")
        e = count(attr, "ERROR")
        pct_valid = 100.0 * v / total
        print(
            f"{label:<20} {v:>8} {s:>10} {m:>10} {e:>8}   "
            f"({pct_valid:.0f}% valid)"
        )
    print("=" * 70)

    # Decision logic
    has_any_valid = [
        p for p in probes
        if p.llms_full == "VALID" or p.llms_txt == "VALID" or p.sitemap == "VALID"
    ]
    has_llms = [
        p for p in probes if p.llms_full == "VALID" or p.llms_txt == "VALID"
    ]
    only_playwright = [
        p for p in probes
        if p.llms_full != "VALID" and p.llms_txt != "VALID" and p.sitemap != "VALID"
    ]
    print(f"\nAt least ONE of three valid:  {len(has_any_valid)}/{total} ({100*len(has_any_valid)/total:.0f}%)")
    print(f"llms.txt or llms-full.txt:     {len(has_llms)}/{total} ({100*len(has_llms)/total:.0f}%)")
    print(f"Need full Crawl4AI/Playwright: {len(only_playwright)}/{total} ({100*len(only_playwright)/total:.0f}%)")

    if only_playwright:
        print("\nFrameworks requiring full crawl (no fast-path available):")
        for p in only_playwright:
            print(f"  - {p.name}  ({p.docs_url})")

    # SPA 200-but-fake cases are interesting — publishers that respond 200
    # to non-existent paths. These frameworks cannot use the naïve
    # "HEAD + check status" heuristic — validation is essential.
    fakes = [p for p in probes if p.llms_full == "SPA_FAKE" or p.llms_txt == "SPA_FAKE"]
    if fakes:
        print(f"\nSPA_FAKE hits (publishers return 200 for missing txt files):")
        for p in fakes[:15]:
            print(
                f"  - {p.name}: full={p.llms_full}({p.llms_full_reason}) "
                f"idx={p.llms_txt}({p.llms_txt_reason})"
            )


async def main():
    print(f"Probing {len(FRAMEWORKS)} frameworks — concurrency=30, timeout=15s")
    print("-" * 70)
    probes = await _run_all(FRAMEWORKS)
    _write_outputs(probes)
    _print_summary(probes)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user", file = sys.stderr)
        sys.exit(130)
