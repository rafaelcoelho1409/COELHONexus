# COELHO Cloud — Embeddings Microservice (TEI)

**Date:** 2026-04-30
**Status:** Architecture decision — research complete, not yet implemented
**Companion docs:**
- `KD-PLANNER-MAP-OPTIMIZATION.md` (the planner step that drove this decision)
- `KD-CLASSICAL-OPTIMIZATION.md` (mentions OP-64 local Qwen3-Embedding which this doc operationalizes)

---

## 1. Decision summary

Deploy a **standalone embeddings microservice in COELHO Cloud** (not as a sidecar of any individual app, not in-process inside FastAPI workers).

- **Implementation:** HuggingFace TEI (Text Embeddings Inference)
- **Model:** `Qwen/Qwen3-Embedding-0.6B`
- **Namespace:** `tei` (or `embeddings` — match COELHO Cloud's existing naming convention for shared services like `postgresql`, `redis`, `qdrant`, `neo4j`, `langfuse`, `minio`)
- **Endpoint:** `http://tei.tei.svc.cluster.local/v1/embeddings` (OpenAI-compatible)
- **Hardware:** CPU-only baseline. GPU optional, drop-in upgrade later.
- **Ownership:** COELHO Cloud platform layer (shared across all current and future projects).

---

## 2. Why a microservice (not sidecar, not in-process)

### Sidecar (rejected)

A sidecar lives in the same pod as another app (e.g., FastAPI). Wrong choice here because:

- The embedding service has **multiple consumers** (FastAPI router, Celery workers in a separate pod, future Go front-end, future Streamlit, future agent runtimes, future search/indexing batch jobs).
- A sidecar couples the embedding lifecycle to one app's pod. Restarting FastAPI for any reason kills embeddings for everyone.
- Network namespace sharing is the only real benefit, and it's negligible at the latency we care about (~1-5ms in-cluster).

### In-process (rejected)

Loading the embedding model inside FastAPI/Celery worker processes is what we have today (fastembed in some paths). Wrong because:

- Each Celery worker carries ~600MB of model weights × N replicas. We typically run 2-4 workers; that's 1.2-2.4GB of redundant memory.
- Cold-start on each new worker spawn (HF Hub download cache hits but model load is still ~5-15s).
- Model upgrade requires redeploying the consumer app.
- No cross-app sharing.

### Microservice (chosen)

- One model loaded in memory, serves all consumers.
- Independent scaling (HPA on TEI's request rate, separate from FastAPI's HTTP load).
- Independent lifecycle: swap `Qwen3-0.6B → Qwen3-1.5B` without redeploying anything else.
- Resource isolation via k8s `resources.limits` — TEI can't starve FastAPI under load.
- Fits the existing COELHO Cloud pattern (`postgresql`, `redis`, `qdrant`, `neo4j`, etc. are already standalone platform services).

---

## 3. Why TEI (not vLLM, Triton, Ollama)

### Considered alternatives

| Tool | Verdict | Why |
|---|---|---|
| **HuggingFace TEI** | ✓ chosen | Purpose-built for embedding serving. Rust+ONNX backend. OpenAI-compat. Production-grade. |
| **vLLM (embedding mode)** | ✗ overkill | Designed for LLM serving with embedding as side feature. Heavier resource footprint. Strongest justification only when also serving LLMs. |
| **NVIDIA Triton + custom backend** | ✗ too complex | Most flexible, most operational overhead. Worth it at scale (>1000 RPS). We don't have that load. |
| **Ollama** | ✗ wrong model class | Ollama prioritizes LLM serving; embedding support is incidental, slower, fewer model options. |
| **Sentence-Transformers in Flask wrapper** | ✗ not production-grade | Custom serving infra. Reinventing batching, metrics, queue management. |

### TEI's feature set (relevant to us)

- **Endpoints:** `POST /embed` (TEI native, fastest), `POST /v1/embeddings` (OpenAI-compatible — drop-in replacement for the NIM API we currently use), `POST /rerank` (cross-encoder support — useful for OP-62 reranker later).
- **Backends:** Rust + ONNX (CPU) or CUDA (GPU). Auto-quantization to int8 with `--dtype int8`.
- **Operational:** Prometheus `/metrics`, `/health`, `/info`, dynamic batching, request queueing, model warmup on startup.
- **Multi-model:** can serve different models on different ports, or run separate Deployments per model.
- **License:** Apache-2.0.

### Model coverage (as of 2026)

Officially supported in TEI (CPU + GPU):

- Qwen3-Embedding family (added in TEI 1.6, September 2025) — our chosen model.
- BAAI/bge-* (m3, large, base, small, reranker)
- intfloat/e5-* (instruct, multilingual)
- thenlper/gte-* (large, base, small)
- mixedbread-ai/mxbai-embed-*
- Snowflake/snowflake-arctic-embed-*
- jinaai/jina-embeddings-*
- sentence-transformers/all-* family
- NV-Embed-v2 (decoder-pooling, TEI 1.5+)

Anything HuggingFace-compatible with a `1_Pooling` config works. CPU mode imposes no architecture restrictions.

---

## 4. Why Qwen3-Embedding-0.6B (not BGE, not Stella, not NV-Embed)

The MAP step (and downstream consumers like REDUCE, resolver fuzzy match, future RAG) embed *technical documentation snippets* — short text (~80-200 chars), code-aware, English-dominant.

### Candidates ranked

| Model | Size | MTEB Code | License | Matryoshka | Verdict |
|---|---|---|---|---|---|
| **Qwen/Qwen3-Embedding-0.6B** | 0.6B | **74** | Apache-2.0 | ✓ (truncate_dim=256) | **✓ chosen** |
| BAAI/bge-base-en-v1.5 | 110M | 66 | MIT | ✗ | current REDUCE fallback (smaller, older) |
| Stella_en_400M_v5 | 400M | 71+ | MIT | partial | strong runner-up; smaller but slightly lower Code score |
| NV-Embed-v2 | 7B | 80+ | MIT | ✗ | too big for our resource budget |
| gte-Qwen2-1.5B-instruct | 1.5B | 73 | Apache-2.0 | ✗ | larger, marginal gain |
| BAAI/bge-m3 | 568M | ~65 (Code) | MIT | ✓ | optimized for retrieval not clustering |

### Why Qwen3-0.6B wins

1. **MTEB Code score 74** — directly relevant because we embed code framework documentation. BGE-base's 66 produces measurably worse cluster boundaries on tech docs.
2. **Matryoshka representation** — truncate to 256d at output time without quality loss. Eliminates the PCA(128) step in the REDUCE pipeline (`reduce_cluster.py:172-192`).
3. **Right size for sidecar memory budget** — 600M params in float32 = ~2.4GB; ONNX int8 quantized via TEI = ~600MB. Fits comfortably in a 2GB container request.
4. **Apache-2.0 license** — clean for shipping COELHO Nexus to customers.
5. **Active maintenance** — Qwen team ships regular updates (2025+).

### Honest tradeoffs

- **Stella_en_400M_v5 is competitive at half the size.** If memory pressure ever surfaces, this is the obvious downsize. MTEB Code drops from 74 → 71 — small but measurable.
- **NV-Embed-v2 is genuinely better but 7B params** — production deployment requires GPU. Defer until we have GPU capacity.
- **bge-m3 is multilingual.** If COELHO Nexus ships to non-English doc corpora (Brazilian Portuguese tech docs, Chinese frameworks, etc.), bge-m3 becomes the right choice. Current scope is English; revisit when it expands.

---

## 5. FastEmbed vs Sentence Transformers vs TEI

A common question: "Why not just keep FastEmbed in-process — isn't it already fast?"

### FastEmbed strengths

- **ONNX runtime backend** — 2-5× faster than Sentence Transformers on CPU.
- **Smaller install footprint** — no PyTorch dependency (~250MB savings).
- **Already in our deps** for the REDUCE fallback path.

### FastEmbed weaknesses for our case

- **Limited model selection.** As of late 2025, FastEmbed does **not** ship Qwen3-Embedding-0.6B. It supports BAAI/bge-base-en-v1.5, jina-v2, and a curated set of older models. MTEB Code ceiling is around 66.
- **No instruction prefixes.** Modern embedding models (Qwen3, NV-Embed, gte-instruct) use a `prompt_name="retrieval.passage"` or similar prefix to condition the embedding. FastEmbed's API doesn't support this.
- **No Matryoshka.** Cannot truncate to 256d at output; you get whatever native dim the model has.
- **In-process only.** Same memory-multiplier-per-worker problem as any in-process pattern.

### Comparison table

| Aspect | FastEmbed (in-process) | Sentence Transformers (in-process) | **TEI (microservice)** |
|---|---|---|---|
| Models for tech docs | bge-base (Code 66), jina-v2 | Qwen3-0.6B, NV-Embed, gte-Qwen2 (Code 74-80) | **Same as ST, served via API** |
| Matryoshka support | ✗ | ✓ | ✓ |
| Instruction prefixes | ✗ | ✓ | ✓ |
| Memory cost | ~250MB per worker | ~600MB per worker | one shared instance for cluster |
| Speed (CPU, batch 40) | ~2-3s | ~5-10s | ~50-100ms (network round-trip + batched inference) |
| Speed (GPU) | n/a | very fast | very fast |
| Multi-consumer reuse | ✗ (per-worker) | ✗ (per-worker) | ✓ (cluster-wide) |
| Requires sidecar deployment | ✗ | ✗ | ✓ |

### Why TEI wins for our shape

1. **Cluster-wide single instance** beats per-worker memory cost.
2. **Quality matters more than latency** at our load (440 embeddings per study, ~1 study/hour at peak). The 50-100ms TEI round-trip is irrelevant; the MTEB Code score 74 → 66 difference is not.
3. **Future GPU upgrade is drop-in** — same TEI binary, same endpoint, same `services/knowledge/embeddings.py` client. Just edit the Helm values to add GPU resources.

### When FastEmbed stays useful

FastEmbed remains the **fallback** path in `services/knowledge/embeddings.py` for the case where TEI is unavailable (network issue, deployment in transition, customer environment without TEI deployed). Same role it has today.

---

## 6. Resource math (CPU + RAM)

CPU-only deployment baseline for `Qwen3-Embedding-0.6B`:

| State | CPU | RAM |
|---|---|---|
| Idle | <100 mCPU | ~800 MB |
| Single batch (40 short docs, ~30-50 tokens each) | 2-4 cores for ~5-10s | ~2-3 GB |
| Sustained planner runs (1 study/min hypothetical) | 1-2 cores avg | ~2 GB stable |

**Resource limits to set:**

```yaml
resources:
  requests:
    cpu: "1"
    memory: "1Gi"
  limits:
    cpu: "4"
    memory: "4Gi"
```

### Cost comparison: shared microservice vs per-project deployment

| Scenario | RAM idle | RAM under load | When it makes sense |
|---|---|---|---|
| Single TEI in COELHO Cloud (this proposal) | ~800MB | ~2-3GB | from project 1, scales to N |
| Per-project TEI × N projects | ~800MB × N | ~2-3GB × N | only if projects are isolated infra |
| Embedding loaded in-process per Celery worker × M workers | ~600MB × M | linear | when you only have one consumer ever |

**Three concrete wins from sharing:**

1. **One model = one memory footprint** regardless of consumer count.
2. **Burst load is uncorrelated across projects** — Nexus's planner run and a future search-indexing job rarely fire at the same moment, so 1 replica handles staggered load.
3. **No per-Celery-worker RAM tax** — externalize the cost once.

By project 2, sharing wins outright on resource cost.

---

## 7. Deployment shape (Kubernetes manifest)

### Minimal v1 — single replica, CPU-only

```yaml
# k8s/embeddings/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: tei

---
# k8s/embeddings/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tei-server
  namespace: tei
spec:
  replicas: 1
  selector:
    matchLabels: {app: tei-server}
  template:
    metadata:
      labels: {app: tei-server}
    spec:
      containers:
      - name: tei
        image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.6
        args:
          - "--model-id=Qwen/Qwen3-Embedding-0.6B"
          - "--dtype=int8"                # ONNX int8 quantization, ~2× speedup
          - "--max-concurrent-requests=512"
          - "--max-batch-tokens=16384"
          - "--max-batch-requests=64"
          - "--port=80"
        ports:
        - containerPort: 80
        resources:
          requests:
            cpu: "1"
            memory: "1Gi"
          limits:
            cpu: "4"
            memory: "4Gi"
        readinessProbe:
          httpGet: {path: /health, port: 80}
          initialDelaySeconds: 30      # model warmup
          periodSeconds: 5
        livenessProbe:
          httpGet: {path: /health, port: 80}
          initialDelaySeconds: 60
          periodSeconds: 30

---
# k8s/embeddings/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: tei
  namespace: tei
spec:
  selector: {app: tei-server}
  ports:
  - port: 80
    targetPort: 80
    name: http
```

Reachable cluster-wide as: `http://tei.tei.svc.cluster.local`

### v2 — HA + autoscaling (add when load justifies)

```yaml
# replicas: 2
# + HorizontalPodAutoscaler on CPU 70% / memory 75%
# + PodDisruptionBudget minAvailable=1
# + Service of type LoadBalancer if external access is needed
```

### v3 — Helm chart structure (when wrapping in Terragrunt module)

```
coelhonexus-cloud-terragrunt/modules/tei/
  Chart.yaml
  values.yaml                    # dev defaults
  values.gpu.yaml                # GPU override
  templates/
    namespace.yaml
    deployment.yaml
    service.yaml
    hpa.yaml
    pdb.yaml
  README.md                      # consumer guide
```

Each customer environment overrides `values.yaml` for their specific resource targets.

---

## 8. Consumers (current + planned)

### Existing consumers in the codebase

1. **KD REDUCE phase** (`apps/fastapi/graphs/knowledge/reduce_cluster.py`) — currently uses NIM API. **Migrate to TEI in the same PR that deploys TEI** to validate the integration on a known-good path.
2. **YouTube RAG** (`apps/fastapi/graphs/youtube/rag.py`) — currently fastembed in-process. Migrate after KD REDUCE is stable.
3. **Resolver fuzzy match** (`apps/fastapi/services/resolver/`) — currently Levenshtein on names. **Optional upgrade**: semantic match using embeddings for "did you mean?" suggestions.

### Near-term consumers (within 6 months)

4. **KD MAP phase** — the work this doc is paired with. New consumer.
5. **OP-62 cross-encoder reranker** — `BAAI/bge-reranker-v2-m3` served via TEI's `/rerank` endpoint. Same TEI deployment, different model.
6. **Cross-study retrieval** — "studies similar to this one" feature. Embeds study titles + plan reasoning.

### Longer-term consumers (6-12 months)

7. **Knowledge-base search** — full-corpus search bar in the front-end. Retrieves chapters + research/raw files.
8. **Agentic RAG layer** — if/when we add an agent that retrieves over user knowledge bases.
9. **Indexing batch jobs** — periodic re-embedding of old corpora when models upgrade.

That's 4 immediate, 3 near-term, 3 longer-term consumers. **Way past the threshold for "deserves a microservice."**

---

## 9. Migration plan

### Phase 1 — Deploy TEI (1 day)

1. Write the manifest (Section 7 above).
2. `kubectl apply -f k8s/embeddings/`
3. Smoke test: `curl http://tei.tei.svc.cluster.local/v1/embeddings -d '{"input":["test"], "model":"Qwen/Qwen3-Embedding-0.6B"}'`
4. Validate `/metrics` exposes Prometheus data.
5. Document the cluster-internal URL in `CLAUDE.md` and `apps/fastapi/services/knowledge/embeddings.py` header comment.

### Phase 2 — Migrate REDUCE (1-2 days)

1. Add `TEI_BASE_URL` env var (default: `http://tei.tei.svc.cluster.local`).
2. Update `services/knowledge/embeddings.py::embed_texts` to call TEI's OpenAI-compat `/v1/embeddings` endpoint as primary, NIM as fallback, fastembed as last resort.
3. Re-run the planner against Terragrunt's cached corpus; verify REDUCE produces the same number of meta-clusters and same chapter structure (deterministic by `_SEED=42`).
4. Once green, demote NIM to second-fallback. Keep it for one release cycle.

### Phase 3 — Build MAP replacement (1 week)

See `KD-PLANNER-MAP-OPTIMIZATION.md` Section 6 (Validation plan). The TEI service deployed here is what the new MAP step will call.

### Phase 4 — Migrate YouTube RAG, resolver fuzzy match (1 week)

Same client refactor pattern. Each migration is independent and reversible.

### Phase 5 — Wrap in Terragrunt module (when ready to ship)

When the coelhonexus-cloud-terragrunt repo starts taking shape (per the cloud-rewrite discussion in the project planning), the TEI manifest becomes the first module wrapped in Terragrunt. Use it as the Terragrunt learning vehicle.

---

## 10. Honest tradeoffs and open questions

### Tradeoff: shared infra means shared upgrade coordination

When `Qwen3-0.6B → Qwen3-1.5B` happens (or any model upgrade), every consumer's embedding-similarity thresholds may drift. A claim that scored 0.42 cosine similarity on the old model might score 0.38 on the new one, etc. Mitigation:

- Pin model version explicitly in the Helm `values.yaml`.
- Treat upgrades as platform releases with versioned changelogs, not silent rolling updates.
- For high-stakes use cases, deploy two TEI Services in parallel during the transition (`tei-v1`, `tei-v2`) and migrate consumers one at a time.

This is the same coordination tax already paid for Postgres, Redis, Qdrant version upgrades. It's a real cost but a known, manageable one.

### Open question: should KeyBERT's in-process model load also go through TEI?

KeyBERT (used in the planner MAP replacement, see `KD-PLANNER-MAP-OPTIMIZATION.md`) needs sub-token attention for keyphrase extraction — TEI's `/v1/embeddings` endpoint can't serve that. So KeyBERT loads the embedding model in-process locally even with TEI deployed. Acceptable cost for MVP; revisit if the dual-model-load (TEI + local KeyBERT) becomes a memory issue.

Possible future fix: TEI 2.x roadmap mentions an `/extract-keyphrases` endpoint experiment. Not promised, not blocking.

### Open question: GPU upgrade timing

CPU-only is enough for current load. When does GPU become worth deploying?

Rough threshold: >5 concurrent embedding requests/second sustained. We're far below that. Revisit when:
- Multiple parallel studies run concurrently (>3/min average).
- Real-time search features ship in the front-end.
- Agentic RAG goes live.

Cost of the GPU upgrade: edit `values.yaml` to add `nvidia.com/gpu: 1` resource limit and use the `cuda-1.6` TEI image instead of `cpu-1.6`. ~10 minutes of operational work.

---

## 11. References

**TEI:**
- [HuggingFace TEI repo](https://github.com/huggingface/text-embeddings-inference)
- [TEI documentation](https://huggingface.co/docs/text-embeddings-inference)
- [TEI Helm chart (community)](https://github.com/huggingface/text-embeddings-inference/tree/main/charts)

**Qwen3-Embedding:**
- [Qwen3-Embedding paper arXiv:2506.05176](https://arxiv.org/abs/2506.05176)
- [Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)

**MTEB:**
- [MTEB benchmark (HuggingFace)](https://huggingface.co/spaces/mteb/leaderboard)
- [Embedding Model Leaderboard March 2026](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/)

**Comparison resources:**
- [Best Open-Source Embedding Models 2026 (BentoML)](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models)
- [NV-Embed paper arXiv:2405.17428](https://arxiv.org/html/2405.17428v3)

**Adjacent decisions:**
- See `KD-PLANNER-MAP-OPTIMIZATION.md` for the planner consumer that drove this decision.
- See `KD-CLASSICAL-OPTIMIZATION.md` OP-64 for the original local-Qwen3 recommendation that this doc operationalizes.
