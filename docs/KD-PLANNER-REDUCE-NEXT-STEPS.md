# KD Planner REDUCE — Next-Step Polishes (resume here)

**Date:** 2026-05-09 (end-of-day)
**Status:** MAP work complete + validated; REDUCE has 3 small known bugs to fix next.
**Companion docs:** `KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` (architecture), `KD-PLANNER-MAP-OPTIMIZATION.md` (MAP picks).

## Where we are

The KD Planner pipeline is **MAP → REDUCE → NAME → ORDER**. Today we shipped + validated the MAP step end-to-end (Phase 7-9: NIM rotator embeddings, classical community-detection, KeyLLM via NIM Llama-3.2-1B + 3B fallback, FastHTML A/B viewer, sanitization polish). Production planner runs `KD_USE_CLASSICAL_MAP=1` by default.

REDUCE was unchanged in this work — it's still the v2 Clio pattern (UMAP + KMeansConstrained + Calinski-Harabasz tiebreaker + size-based k_target + slug dedup) with T-2 (size_max cap) and T-3 (thin-chapter merge) added in Phase 1b.

**Two corpora ran end-to-end through the production planner:**
- Terragrunt (440 → 5 chapters, 3.4 min total) — clean
- Docker (1318 → 8 chapters, ~7 min total) — surfaced 3 REDUCE quality bugs

## Next step — 3 REDUCE polishes (~30 min total)

All in `apps/fastapi/graphs/knowledge/reduce_cluster.py`.

### Polish #1 — Empty-title guard (~5 LoC)

**Symptom:** Docker run produced Chapter 6 with title `""` (empty string). The `kd-all` rotator returned an empty string instead of `None` for that one meta-cluster, slipping past the existing `None` check in `_label_one`.

**Fix location:** `_label_one()` inside `embed_and_cluster_reduce()`, after the `await label_chain.ainvoke(...)` call where it currently checks `if draft is None`.

**Change:**
```python
# Currently:
if draft is None:
    raise RuntimeError(...)

# Make it:
if draft is None or not (draft.title or "").strip():
    raise RuntimeError(
        f"label_chain returned None or empty title for meta {meta_id}"
    )
```

The existing `except Exception` block already builds a synthetic `MetaLabelDraft(title=f"{seed_name} and Related", ...)` — so empty titles will now route through that fallback instead of being silently emitted.

### Polish #2 — One retry before synthetic fallback (~15 LoC)

**Symptom:** Docker run produced Chapter 2 with title `"DEACTIVATING A DOCKER ACCOUNT and Related"` — the synthetic fallback (`"{seed_name} and Related"`). One specific kd-all rotator deployment returned malformed structured output once; the existing exception path went straight to synthetic without retrying through the rotator's other 36 deployments.

**Fix location:** Same `_label_one()` inside `embed_and_cluster_reduce()`. Wrap the `label_chain.ainvoke(...)` call in a small retry loop — on first failure, retry once before falling through to synthetic.

**Change (sketch):**
```python
draft = None
last_err = None
for attempt in range(2):  # 1 initial + 1 retry
    try:
        draft = await label_chain.ainvoke({...}, config=...)
        if draft is not None and (draft.title or "").strip():
            break
        last_err = RuntimeError(f"empty draft on attempt {attempt+1}")
    except Exception as e:
        last_err = e
    draft = None
if draft is None:
    logger.warning(f"[reduce-cluster] meta {meta_id} ... after retry; using synthetic")
    # existing synthetic fallback
    seed_name = ...
    draft = MetaLabelDraft(title=f"{seed_name} and Related", goal=...)
```

The Router's per-deployment cooldown means the retry is likely to land on a different model. Note: avoid a retry storm — cap at 2 attempts total (1 + 1 retry).

### Polish #3 — Tighten T-2 cap from 0.25 → 0.20 (1 LoC)

**Symptom:** Docker Chapter 7 was 225 files (22% of corpus) — borderline junk drawer. The current T-2 cap was `_META_CLUSTER_MAX_FRACTION = 0.25` (so a single meta-cluster could absorb up to 25% of total micro-clusters). Tightening to 0.20 forces no chapter beyond ~20% of corpus.

**Fix location:** `reduce_cluster.py` module-level constant.

**Change:**
```python
# Currently:
_META_CLUSTER_MAX_FRACTION = 0.25
# Change to:
_META_CLUSTER_MAX_FRACTION = 0.20
```

Trade-off: at small N (k=5, n_clusters=48), 0.20 × 48 = 9.6 → ceil(9.6) = 10 micro-clusters per meta. KMeansConstrained may produce more chapters as a result (good — finer granularity). At large N (k=8, n_clusters=135), 0.20 × 135 = 27 micro-clusters per meta. Still gives KMeansConstrained breathing room.

## Validation plan (after shipping the 3 polishes)

1. **Stop + restart skaffold** to redeploy the updated FastAPI image.
2. **Re-run Docker planner**:
   ```bash
   curl -X POST "http://localhost:23020/api/v1/knowledge/studies?stop_after=planner" \
     -H "Content-Type: application/json" \
     -d '{"framework":"Docker"}'
   ```
3. **Verify in the resulting plan**:
   - No empty-string chapter titles
   - No `"... and Related"` synthetic fallback titles
   - No chapter with >20% of corpus files
4. **Optional re-run on Terragrunt** to confirm no regression — should still produce ~5 clean chapters.

If all three issues are gone on Docker, ship + commit the polishes.

## Quick state check on resume tomorrow

```bash
# 1. Confirm where you are
cd ~/Workbench/COELHONexus && git status && git log --oneline -5

# 2. Confirm the env flag is still set
grep useClassicalMap k8s/helm/values.yaml      # → "1"
grep KD_USE_CLASSICAL_MAP k8s/helm/templates/_helpers.tpl

# 3. Confirm the constants you'll edit
grep -n "_META_CLUSTER_MAX_FRACTION" apps/fastapi/graphs/knowledge/reduce_cluster.py
grep -n "_label_one" apps/fastapi/graphs/knowledge/reduce_cluster.py
```

## Out of scope for this batch (defer)

- **MLflow tracking** — user explicitly skipped earlier.
- **Reranker for synthesizer** (Cohere `rerank-v3.5`) — next major feature work, ~3-4 h.
- **Full distiller run** (no `stop_after`) — validation milestone after REDUCE polishes land.
- **Run on MLflow / FastAPI / Python corpora** — generality check; not blocking REDUCE polishes.
- **Deeper REDUCE knobs** (HDBSCAN, Nomic-embed clustering prefix, Agglomerative) — deferred per Clio doc; only revisit if v2 + the 3 polishes still produce junk drawers.

## Files of interest tomorrow

| File | What you'll touch |
|---|---|
| `apps/fastapi/graphs/knowledge/reduce_cluster.py` | All 3 polishes |
| `docs/KNOWLEDGE-DISTILLER-REDUCE-CLIO-PATTERN.md` | Reference (architecture) — read §"Known quality issues" if you want context |
| `docs/KD-PLANNER-MAP-OPTIMIZATION.md` | Reference (MAP picks committed earlier today) |

That's it — pick this up tomorrow, ship the 3 polishes, validate on Docker, commit. Should be a 1-hour focused session.
