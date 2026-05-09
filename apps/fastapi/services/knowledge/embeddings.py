"""
Knowledge Distiller — Dense Embeddings Service (Xinference + XinfManager)

Single source of truth for every embedding call in KD. The XinfManager class
turns Xinference's launch/terminate API into a single-slot model swap with
safe concurrent use across FastAPI + Celery via a Redis transition lock.

Used by:
  - graphs/knowledge/reduce_cluster.py     (REDUCE: micro-cluster embeddings)
  - graphs/knowledge/hierarchical_synth.py (synth audit: section + hash vecs)
  - graphs/knowledge/preview.py            (preview clustering)
  - graphs/knowledge/classical_map.py      (Planner MAP step replacement)
  - graphs/knowledge/helpers.py            (semantic off-topic noise filter)

Design (decoupled lifecycle):
  - The transition lock is held ONLY during eviction + launch. Once a model
    is loaded, embed calls proceed lock-free — same-model concurrent callers
    do not serialize. Different-model callers serialize across the swap.
  - The 8 GiB Xinference container holds exactly one embedding model at a
    time. Adding a second model class (rerankers, multilingual, code) just
    means adding another entry to MODEL_PAYLOADS — no infra change needed.
  - 404-on-embed (model evicted by another process) auto-recovers: re-runs
    the transition path and retries the batch once.

Public API:
  embed_texts(texts, model_name=...)       -> (vectors, provider_label)   # async
  embed_texts_sync(texts, model_name=...)  -> (vectors, provider_label)   # sync
  community_detection(embeddings, threshold, min_community_size)          # numpy
  smoke_test(mode=...)                     -> dict                        # /debug
  ensure_embedding_model_async()           -> None                        # FastAPI lifespan
  ensure_embedding_model_sync()            -> None                        # Celery worker init
  get_manager()                            -> XinfManager                 # singleton

Modes (env var KD_EMBEDDING_MODE, override via mode=... per call):
  "xinference_with_fallback" (default) — try Xinference, fall back to fastembed
  "xinference"                          — Xinference only, raise on failure
  "local"                               — fastembed only (skip Xinference)
"""
import asyncio
import logging
import math
import os
import threading
import time
from typing import Optional

import httpx
import numpy as np
import redis as redis_sync


logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================
_MODE = os.environ.get("KD_EMBEDDING_MODE", "xinference_with_fallback").strip().lower()

_XINFERENCE_URL = os.environ.get(
    "XINFERENCE_BASE_URL",
    "http://xinference.xinference.svc.cluster.local:9997",
).rstrip("/")

_LOCAL_MODEL = os.environ.get(
    "KD_EMBEDDING_MODEL_LOCAL",
    "BAAI/bge-base-en-v1.5",
)

_BATCH_SIZE = int(os.environ.get("KD_XINFERENCE_BATCH_SIZE", "64"))
# Cold first POST /v1/models triggers HF download + load; budget 600s.
_LOAD_TIMEOUT_S = float(os.environ.get("KD_XINFERENCE_LOAD_TIMEOUT_S", "600"))
# Warm embedding call typically <5s; cap tail at 120s.
_EMBED_TIMEOUT_S = float(os.environ.get("KD_XINFERENCE_EMBED_TIMEOUT_S", "120"))
# Cross-process transition lock TTL — covers cold model load.
_TRANSITION_LOCK_TTL_S = 600
_TRANSITION_POLL_INTERVAL_S = 0.5

DEFAULT_MODEL = "Qwen3-Embedding-0.6B"


def _redis_url() -> str:
    """Build Redis URL from env vars (mirrors celery_app.py / app.py)."""
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    pw = os.environ.get("REDIS_PASSWORD", "")
    return f"redis://:{pw}@{host}:{port}/0" if pw else f"redis://{host}:{port}/0"


# =============================================================================
# MODEL_PAYLOADS — single source of truth for /v1/models launch JSON
# =============================================================================
# Add a new entry here to introduce another model class. The XinfManager
# transparently swaps between any keys present in this dict.
#
# CRITICAL: omit `n_gpu` on CPU deployments. Xinference's validator rejects
# integer 0 with HTTP 400 ("Invalid n_gpu value"). The default ("auto") picks
# correctly on CPU-only nodes. Setting it explicitly to a positive int is
# only for GPU nodes.
MODEL_PAYLOADS: dict[str, dict] = {
    "Qwen3-Embedding-0.6B": {
        "model_engine":  "llama.cpp",
        "model_name":    "Qwen3-Embedding-0.6B",
        "model_type":    "embedding",
        "model_format":  "ggufv2",
        "quantization":  "Q8_0",
        "replica":       1,
    },
    # Llama-3.2-1B-Instruct — KeyLLM step in graphs.knowledge.classical_map.
    # Picked over Qwen3-0.6B / Qwen2.5-0.5B because:
    #   - IFEval 59.5 (highest among ≤1B candidates) — best at "follow the
    #     2-4 word Title Case format" constraint we use for cluster labels
    #   - Temp=0 deterministic decoding works without caveat (Qwen3 team
    #     explicitly warns against greedy decoding for sub-1B models)
    #   - Q4_K_M GGUF ~808MB; ~25-35 tok/s on Tiger Lake CPU
    # See docs/KD-PLANNER-MAP-OPTIMIZATION.md §5 for the full rationale.
    "llama-3.2-instruct": {
        "model_engine":            "llama.cpp",
        "model_name":              "llama-3.2-instruct",
        "model_type":              "LLM",
        "model_format":            "ggufv2",
        "model_size_in_billions":  1,
        "quantization":            "Q4_K_M",
        "replica":                 1,
    },
    # Future entries (reranker, multilingual, code-aware) plug in here.
    # Examples kept commented so the schema is visible at a glance.
    # "bge-reranker-v2-m3": {
    #     "model_engine": "transformers",
    #     "model_name":   "bge-reranker-v2-m3",
    #     "model_type":   "rerank",
    #     "replica":      1,
    # },
}


# =============================================================================
# XinfManager — single-slot serialized model swap
# =============================================================================
class XinfManager:
    """
    Manages Xinference model lifecycle with safe concurrent use across
    FastAPI + Celery.

    Coordination strategy:
      - Per-process asyncio.Lock + threading.Lock guard intra-process
        transitions.
      - Cross-process Redis lock (key `xinf:transition`) prevents two
        processes from racing the eviction + launch sequence.
      - Embeds happen LOCK-FREE once a model is loaded — same-model
        concurrent callers proceed in parallel. Only model SWAPS serialize.
      - 404-on-embed (model evicted by another process) auto-recovers via
        a single re-transition + retry inside `_embed_sync`.

    Crash safety: the Redis transition lock has TTL=600s. If a process dies
    mid-launch, the lock self-frees and other processes proceed.
    """

    def __init__(self, base_url: str, redis_url: str):
        self.base_url = base_url.rstrip("/")
        self._redis = redis_sync.Redis.from_url(redis_url)
        self._transition_key = "xinf:transition"
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    # ---- HTTP primitives (sync — async via asyncio.to_thread) -----------
    def _get_loaded_sync(self) -> set[str]:
        with httpx.Client(timeout=_EMBED_TIMEOUT_S) as c:
            r = c.get(f"{self.base_url}/v1/models")
            r.raise_for_status()
            return {m.get("id") for m in r.json().get("data", [])}

    async def _get_loaded_async(self) -> set[str]:
        return await asyncio.to_thread(self._get_loaded_sync)

    def _launch_sync(self, payload: dict) -> None:
        with httpx.Client(timeout=_LOAD_TIMEOUT_S) as c:
            r = c.post(f"{self.base_url}/v1/models", json=payload)
            r.raise_for_status()

    def _terminate_sync(self, model_uid: str) -> None:
        with httpx.Client(timeout=_LOAD_TIMEOUT_S) as c:
            r = c.delete(f"{self.base_url}/v1/models/{model_uid}")
            # 404 = already gone (race with another process). Fine.
            if r.status_code not in (200, 204, 404):
                r.raise_for_status()

    # ---- Transition (eviction + launch) under cross-process Redis lock --
    def _transition_sync(self, model: str) -> None:
        """Sync transition: ensure `model` is loaded, evicting others if needed."""
        payload = MODEL_PAYLOADS[model]
        with self._sync_lock:
            loaded = self._get_loaded_sync()
            if model in loaded:
                return
            t_start = time.monotonic()
            while True:
                got = self._redis.set(
                    self._transition_key, model,
                    nx=True, ex=_TRANSITION_LOCK_TTL_S,
                )
                if got:
                    break
                if time.monotonic() - t_start > _TRANSITION_LOCK_TTL_S:
                    raise TimeoutError(
                        f"Xinference transition lock timeout for {model!r}"
                    )
                time.sleep(_TRANSITION_POLL_INTERVAL_S)
            try:
                # Re-check inside lock (another process may have just loaded it)
                loaded = self._get_loaded_sync()
                if model in loaded:
                    return
                # Evict every other model — single-slot invariant
                for other in loaded:
                    if other != model:
                        logger.info(f"[xinf] swap: terminating {other!r}")
                        self._terminate_sync(other)
                logger.info(
                    f"[xinf] launching {model!r} on {self.base_url} "
                    f"(cold start may take ~30-90s for HF download)"
                )
                t0 = time.monotonic()
                self._launch_sync(payload)
                logger.info(
                    f"[xinf] {model!r} ready ({time.monotonic() - t0:.1f}s)"
                )
            finally:
                # Best-effort release; if TTL already expired, no-op.
                try:
                    self._redis.delete(self._transition_key)
                except Exception:
                    pass

    async def _transition_async(self, model: str) -> None:
        """Async wrapper for `_transition_sync`."""
        async with self._async_lock:
            await asyncio.to_thread(self._transition_sync_no_local_lock, model)

    def _transition_sync_no_local_lock(self, model: str) -> None:
        """Like _transition_sync but assumes intra-process lock already held
        by the async path. Avoids re-entrant lock acquisition."""
        payload = MODEL_PAYLOADS[model]
        loaded = self._get_loaded_sync()
        if model in loaded:
            return
        t_start = time.monotonic()
        while True:
            got = self._redis.set(
                self._transition_key, model,
                nx=True, ex=_TRANSITION_LOCK_TTL_S,
            )
            if got:
                break
            if time.monotonic() - t_start > _TRANSITION_LOCK_TTL_S:
                raise TimeoutError(
                    f"Xinference transition lock timeout for {model!r}"
                )
            time.sleep(_TRANSITION_POLL_INTERVAL_S)
        try:
            loaded = self._get_loaded_sync()
            if model in loaded:
                return
            for other in loaded:
                if other != model:
                    logger.info(f"[xinf] swap: terminating {other!r}")
                    self._terminate_sync(other)
            logger.info(
                f"[xinf] launching {model!r} on {self.base_url} "
                f"(cold start may take ~30-90s for HF download)"
            )
            t0 = time.monotonic()
            self._launch_sync(payload)
            logger.info(
                f"[xinf] {model!r} ready ({time.monotonic() - t0:.1f}s)"
            )
        finally:
            try:
                self._redis.delete(self._transition_key)
            except Exception:
                pass

    # ---- Public embed API ------------------------------------------------
    def _embed_call_sync(
        self, model: str, batch: list[str], retried_404: bool = False,
    ) -> list[list[float]]:
        """Single batch POST /v1/embeddings with one 404-recovery retry."""
        with httpx.Client(timeout=_EMBED_TIMEOUT_S) as c:
            r = c.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": model, "input": batch},
            )
            if r.status_code == 404 and not retried_404:
                # Model was evicted between our load-check and embed call.
                # Re-transition once and retry.
                logger.info(
                    f"[xinf] embed got 404 for {model!r}; re-transitioning + retry"
                )
                self._transition_sync(model)
                return self._embed_call_sync(model, batch, retried_404=True)
            r.raise_for_status()
            return [item["embedding"] for item in r.json()["data"]]

    def embed_sync(self, model: str, texts: list[str]) -> list[list[float]]:
        """Sync batch-embed via Xinference. Idempotent ensure-loaded; auto-batched.

        Empty/whitespace inputs get substituted with " " to keep batch index
        alignment (the resulting vector is meaningless but the caller's index
        stays valid).
        """
        if not texts:
            return []
        if model not in MODEL_PAYLOADS:
            raise KeyError(
                f"Unknown model {model!r}; add to MODEL_PAYLOADS in embeddings.py"
            )
        loaded = self._get_loaded_sync()
        if model not in loaded:
            self._transition_sync(model)
        clean = [t if (t and t.strip()) else " " for t in texts]
        out: list[list[float]] = []
        for start in range(0, len(clean), _BATCH_SIZE):
            batch = clean[start:start + _BATCH_SIZE]
            out.extend(self._embed_call_sync(model, batch))
        if len(out) != len(texts):
            raise RuntimeError(
                f"Xinference returned {len(out)} embeddings for {len(texts)} inputs"
            )
        return out

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Async batch-embed (runs sync path in a worker thread)."""
        return await asyncio.to_thread(self.embed_sync, model, texts)

    # ---- Chat completion (KeyLLM, small task LMs) ------------------------
    def _chat_call_sync(
        self,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        retried_404: bool = False,
    ) -> str:
        """Single POST /v1/chat/completions with one 404-recovery retry."""
        with httpx.Client(timeout=_EMBED_TIMEOUT_S) as c:
            r = c.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            if r.status_code == 404 and not retried_404:
                logger.info(
                    f"[xinf] chat got 404 for {model!r}; re-transitioning + retry"
                )
                self._transition_sync(model)
                return self._chat_call_sync(
                    model, messages, temperature, max_tokens, retried_404=True,
                )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    def chat_sync(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 32,
    ) -> str:
        """
        Sync chat completion via Xinference's OpenAI-compatible endpoint.
        Idempotent ensure-loaded; same single-slot swap rules as embed_sync.

        Args:
            model:       MODEL_PAYLOADS key (e.g., "llama-3.2-instruct").
            messages:    OpenAI chat format ([{"role": "system|user", "content": "..."}]).
            temperature: 0.0 for deterministic outputs (default).
            max_tokens:  cap output length. Default 32 covers cluster-label use.

        Returns:
            The assistant message content as a plain string.
        """
        if model not in MODEL_PAYLOADS:
            raise KeyError(
                f"Unknown model {model!r}; add to MODEL_PAYLOADS in embeddings.py"
            )
        loaded = self._get_loaded_sync()
        if model not in loaded:
            self._transition_sync(model)
        return self._chat_call_sync(model, messages, temperature, max_tokens)

    async def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 32,
    ) -> str:
        """Async chat completion (runs sync path in a worker thread)."""
        return await asyncio.to_thread(
            self.chat_sync, model, messages, temperature, max_tokens,
        )

    # ---- Connectivity check (used by FastAPI/Celery startup) ------------
    def ping(self) -> bool:
        """Cheap connectivity probe. Returns True if Xinference responds, else False."""
        try:
            self._get_loaded_sync()
            return True
        except Exception as e:
            logger.warning(
                f"[xinf] ping failed ({type(e).__name__}: {str(e)[:160]})"
            )
            return False


# =============================================================================
# Module-singleton XinfManager (lazy-instantiated)
# =============================================================================
_manager: Optional[XinfManager] = None
_manager_lock = threading.Lock()


def get_manager() -> XinfManager:
    """Return the process-wide XinfManager singleton.
    Constructed lazily on first call; safe under threading."""
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is not None:
            return _manager
        _manager = XinfManager(base_url=_XINFERENCE_URL, redis_url=_redis_url())
        return _manager


# =============================================================================
# fastembed fallback (preserved — kicks in when KD_EMBEDDING_MODE allows)
# =============================================================================
_local_lock = threading.Lock()
_local_instance = None  # type: Optional["TextEmbedding"]  # noqa: F821


def _get_local_model():
    global _local_instance
    if _local_instance is not None:
        return _local_instance
    with _local_lock:
        if _local_instance is not None:
            return _local_instance
        from fastembed import TextEmbedding
        logger.info(f"[embeddings] loading fastembed model {_LOCAL_MODEL!r}")
        _local_instance = TextEmbedding(model_name=_LOCAL_MODEL)
        logger.info(f"[embeddings] fastembed {_LOCAL_MODEL!r} ready")
        return _local_instance


def _embed_local_sync(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _get_local_model()
    return [v.tolist() for v in model.embed(texts)]


# =============================================================================
# Public API — embed_texts (sync + async)
# =============================================================================
def embed_texts_sync(
    texts: list[str],
    mode: Optional[str] = None,
    model_name: str = DEFAULT_MODEL,
) -> tuple[list[list[float]], str]:
    """
    Synchronous batch embed. Returns (vectors, provider_label).

    Args:
        texts: list of strings to embed.
        mode: override KD_EMBEDDING_MODE for this call. One of:
              "xinference_with_fallback" | "xinference" | "local".
        model_name: which Xinference model to use. Must be a key in
                    MODEL_PAYLOADS. Defaults to Qwen3-Embedding-0.6B.

    provider_label is "xinference:<model>" or "local:<model>" so callers can
    log which backend served the request on fallback cases.
    """
    effective = (mode or _MODE).lower()
    if not texts:
        return [], "empty"

    if effective == "local":
        return _embed_local_sync(texts), f"local:{_LOCAL_MODEL}"

    if effective == "xinference":
        vectors = get_manager().embed_sync(model_name, texts)
        return vectors, f"xinference:{model_name}"

    # Default: Xinference with local fallback
    try:
        t0 = time.time()
        vectors = get_manager().embed_sync(model_name, texts)
        logger.info(
            f"[embeddings] xinference {model_name} ok "
            f"({len(texts)} items in {time.time() - t0:.2f}s)"
        )
        return vectors, f"xinference:{model_name}"
    except Exception as e:
        logger.warning(
            f"[embeddings] xinference {model_name} failed "
            f"({type(e).__name__}: {str(e)[:160]}); "
            f"falling back to local fastembed"
        )
        t0 = time.time()
        vectors = _embed_local_sync(texts)
        logger.info(
            f"[embeddings] local {_LOCAL_MODEL} ok "
            f"({len(texts)} items in {time.time() - t0:.2f}s)"
        )
        return vectors, f"local:{_LOCAL_MODEL}"


async def embed_texts(
    texts: list[str],
    mode: Optional[str] = None,
    model_name: str = DEFAULT_MODEL,
) -> tuple[list[list[float]], str]:
    """Async wrapper — runs the sync embedder in a worker thread."""
    return await asyncio.to_thread(embed_texts_sync, texts, mode, model_name)


# =============================================================================
# community_detection — pure-Python greedy O(N²) cosine clustering
# =============================================================================
# Drop-in for sentence_transformers.util.community_detection without the
# torch dependency. Deterministic and fast at our N≤200 scale.
def community_detection(
    embeddings: np.ndarray,
    threshold: float = 0.6,
    min_community_size: int = 2,
) -> list[list[int]]:
    """
    Greedy O(N²) cosine-based community detection.

    Args:
        embeddings: (N, D) array. Will be L2-normalized internally.
        threshold:  cosine similarity required for community membership.
        min_community_size: minimum members for a valid community.

    Returns:
        List of communities (each a sorted list of indices into `embeddings`),
        ordered by size descending. Indices not in any returned community are
        treated as "singletons / unused" by callers.
    """
    arr = np.asarray(embeddings, dtype=np.float32)
    n = len(arr)
    if n == 0:
        return []
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    normalized = arr / np.maximum(norms, 1e-12)
    sim = normalized @ normalized.T  # (N, N)
    # For each anchor row, the candidate members above threshold
    candidates: list[list[int]] = []
    for i in range(n):
        members = np.where(sim[i] >= threshold)[0].tolist()
        if len(members) >= min_community_size:
            candidates.append(sorted(members))
    # Resolve overlaps: largest cluster wins; subsequent clusters drop members
    # already claimed. Stable tiebreak on smallest anchor index for determinism.
    candidates.sort(key=lambda m: (-len(m), m[0] if m else 0))
    used: set[int] = set()
    communities: list[list[int]] = []
    for members in candidates:
        unique = [m for m in members if m not in used]
        if len(unique) >= min_community_size:
            communities.append(sorted(unique))
            used.update(unique)
    return communities


# =============================================================================
# smoke_test — quick sanity check for /debug/embeddings_smoke
# =============================================================================
def smoke_test(mode: Optional[str] = None) -> dict:
    """
    Verify the embeddings stack: round-trip works AND cosine geometry is sane.
    Returns a dict with provider, dim, similarity scores, margin, ok=True.
    Raises on geometry failure.
    """
    test_texts = [
        "terragrunt configuration --- Configure terragrunt.hcl with options",
        "configure terragrunt --- Set up terragrunt configuration files",
        "kubernetes deployment --- Deploy applications to a kubernetes cluster",
    ]
    vectors, provider = embed_texts_sync(test_texts, mode=mode)
    if len(vectors) != 3:
        raise RuntimeError(f"smoke: expected 3 vectors, got {len(vectors)}")

    def _cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        ma = math.sqrt(sum(x * x for x in a))
        mb = math.sqrt(sum(x * x for x in b))
        return dot / (ma * mb) if (ma and mb) else 0.0

    sim_close = _cos(vectors[0], vectors[1])
    sim_far = _cos(vectors[0], vectors[2])
    if sim_close <= sim_far:
        raise RuntimeError(
            f"smoke: similar pair ({sim_close:.3f}) "
            f"<= different pair ({sim_far:.3f}) — geometry broken"
        )
    return {
        "provider": provider,
        "dim": len(vectors[0]),
        "sim_close": round(sim_close, 4),
        "sim_far": round(sim_far, 4),
        "margin": round(sim_close - sim_far, 4),
        "ok": True,
    }


# =============================================================================
# Backward-compat shims (used by app.py + celery_app.py + earlier callers)
# =============================================================================
# These now route through XinfManager — concurrent callers across processes
# coordinate via the Redis transition lock; only one launches.
def ensure_embedding_model_sync() -> None:
    """Pre-warm DEFAULT_MODEL. Idempotent. Safe under concurrent calls."""
    mgr = get_manager()
    loaded = mgr._get_loaded_sync()
    if DEFAULT_MODEL not in loaded:
        mgr._transition_sync(DEFAULT_MODEL)


async def ensure_embedding_model_async() -> None:
    """Async wrapper for the FastAPI lifespan startup hook."""
    await asyncio.to_thread(ensure_embedding_model_sync)
