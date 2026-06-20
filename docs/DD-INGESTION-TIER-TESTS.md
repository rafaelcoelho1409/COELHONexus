# DD Ingestion — per-tier smoke test picks

Quick reference for validating the ingestion download pipeline end-to-end after any change to `apps/fastapi/domains/dd/ingestion/tiers/*`. Run in order; fail-fast on the cheapest first.

---

## The picks

| Tier | Source pattern | **Best test pick** | Why this one | Approx. corpus |
|---|---|---|---|---|
| **1** | `llms-full.txt` (single bundle) | **Pydantic** | Stable, recognizable content, ~50-100 KB single file, fast to verify | ~20-40 split pages |
| **2** | `llms.txt` (link index) | **FastHTML** | Only 2 candidates in this tier; FastHTML is the framework the UI itself runs on — easy to spot bad output | ~10-20 pages |
| **3** | `sitemap.xml` | **FastAPI** | Popular, reliable sitemap structure, well-known content, large enough to exercise concurrency | ~80-150 pages |
| **4** | Sphinx + `objects.inv` (autodoc-heavy) | **NumPy** | Directly stresses Tier 4's BFS + autodoc filter path; objects.inv proven to resolve (2735 doc pages, 7183 entities) | ~2700 pages |
| **5** | GitHub-only crawler | *(none)* | **Zero catalog entries currently route here** — the dispatch path exists but no framework declares GitHub-only as its source |

---

## Run order — fast → slow

1. **Pydantic** (Tier 1) — ~10 s
2. **FastHTML** (Tier 2) — ~20 s
3. **FastAPI** (Tier 3) — ~1-2 min
4. **NumPy** (Tier 4) — ~5-10 min

If Pydantic + FastHTML + FastAPI succeed but NumPy fails, the regression is isolated to Tier 4 download logic. If all 4 succeed, the entire ingestion download pipeline is healthy.

---

## What these tests do and do NOT cover

**Cover** (the download pipeline — what regressions on `tiers/*` actually break):

- Source resolution (which tier picks each framework)
- Per-tier fetch logic (httpx GET, objects.inv parse, sitemap parse, Sphinx toctree, Playwright fallback)
- Page splitting + sentinelization
- MinIO write of `ingestion/<slug>/pages/*.md` + `ingestion/<slug>/artifacts/*`

**Do NOT cover**:

- The `doc_distill` LLM stage of the **Planner** — that needs a working LLM provider (NVIDIA NIM canonical, free; configure via `/settings` BYOK UI or `NVIDIA_API_KEY` in `.env`). With empty keys, every distill returns `parse_fail` and falls back to the deterministic placeholder distillate. Pages still land in MinIO, but the planner's chapter outline will be built on placeholders.
- The Synth pipeline downstream (its own CoRefine loop, separate concern).

So: **these tests verify the download phases**, not the LLM phases. If you see all 4 ingest successfully into MinIO but the planner produces meaningless chapter titles, the issue is the BYOK key drought, not the ingestion code.

---

## Verifying success without using the UI

After kicking off an ingestion from the Catalog tab, you can confirm pages landed in MinIO directly:

```bash
export KUBECONFIG=$HOME/Workbench/COELHONexus/infrastructure/live/coelhonexus/00-bootstrap/k3d/kubeconfig
kubectl -n minio exec -i deploy/minio -- mc alias set local http://localhost:9000 minioadmin minioadmin 2>/dev/null
kubectl -n minio exec -i deploy/minio -- mc ls --recursive local/coelhonexus 2>/dev/null \
  | grep -E "ingestion/(pydantic|fasthtml|fastapi|numpy)/" | head -20
```

Empty output for a given slug = the download phase failed for that tier. Compare against the Celery log's `[task] run_ingestion ... status=` line.

---

## Catalog index of all tiers (for reference)

At time of writing the catalog has:

- **Tier 1** (llms_full): 14 entries — Browser Use, Claude Code, Dask, dbt, Evidently, FastMCP, Inference (Roboflow), Locust, **Pydantic**, Qdrant, …
- **Tier 2** (llms_txt only): 2 entries — **FastHTML**, Optimum
- **Tier 3** (sitemap only): 48 entries — Alibi Explain, Argo CD, CatBoost, Crawl4AI, Dagster, Docker, DuckDB (Python), Elasticsearch (Python), **FastAPI**, FastEmbed, …
- **Tier 4** (Sphinx-only): 43 entries — ADTK, Ansible, Apache Airflow, Asyncio, Bash, Celery (Python), Delta Lake (Python), Go, Kafka (Python), Kubeflow, **NumPy**, Pandas, Scikit-Learn, PyTorch Lightning, …
- **Tier 5** (github only): 0 entries

The full catalog lives at `apps/fastapi/domains/dd/resolver/sources.yaml`. Run this Python one-liner to recompute the tier distribution after catalog edits:

```bash
python3 -c "
import yaml
data = yaml.safe_load(open('apps/fastapi/domains/dd/resolver/sources.yaml'))['frameworks']
tiers = {1:[], 2:[], 3:[], 4:[], 5:[]}
for e in data:
    if   e.get('llms_full'): tiers[1].append(e['name'])
    elif e.get('llms_txt'):  tiers[2].append(e['name'])
    elif e.get('sitemap'):   tiers[3].append(e['name'])
    elif e.get('docs'):      tiers[4].append(e['name'])
    elif e.get('github'):    tiers[5].append(e['name'])
for t,n in tiers.items(): print(f'Tier {t}: {len(n)} entries')
"
```
