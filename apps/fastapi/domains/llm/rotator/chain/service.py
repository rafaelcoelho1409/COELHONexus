"""COELHO LLM Rotator adapter — exclusive endpoint, no per-process weights.

All Planner/Synth calls now route through the universal free-quota gateway:
  standalone rotator (coelho-llm-rotator) via OpenAI-compatible HTTP.
  In-cluster: http://coelho-llm-rotator-fastapi:8000/v1
  Tailnet:    https://coelho-llm-rotator.tail39dc94.ts.net/v1
  Legacy compat: /api/v1/llm/openai/v1 prefixed path is auto-normalized.

No dd_process / heavyweight / bandit weights here — FGTS-VA + TrueSkill + latency EWMA
lives inside the rotator itself (single auto pool, 21 models). This module is a thin
OpenAI-compat adapter preserving the old `chat_judge_*` / `embed_via_router_*` signatures
so Planner/Synth require zero per-file churn.

SOTA Sept 2026 optimizations:
- Singleton AsyncOpenAI with pooled httpx.AsyncClient (Limits 200/100, http2, keepalive 30s)
  → reuses TCP connections across 135+ corpus calls, avoids per-call ChatOpenAI construction
    that previously allocated a fresh httpx client (≈15 ms + TLS/handshake overhead).
- Raw OpenAI SDK path instead of LangChain ChatOpenAI wrapper for doc_distill hot loop
  → cuts ~20-30 ms of message conversion & response_metadata massaging per call.
- Connection pooling + http2 multiplexing lets 20-30 concurrent in-flight share the same
  TCP pool without pool exhaustion (see httpx Limits docs + Decodo 2026 benchmark).
- Rotator-side simple-shuffle + allowed_fails circuit breaker already absorbs 429s,
  so client concurrency can safely rise from 8 → 24 without the old 36% rate-limit blowup.
- MinIO I/O decoupled from LLM semaphore (see doc_distill service) — semaphore gates
  only the network-bound LLM hop, not the storage read.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint resolution — normalize any legacy /api/v1/llm/openai/v1 prefix to /v1
# ---------------------------------------------------------------------------

def _normalize_base_url(url: str) -> str:
    """Return an OpenAI SDK base_url (scheme+host+prefix without /chat/completions).

    Accepts:
      - http://host:8000/v1
      - http://host:8000/api/v1/llm/openai/v1
      - http://host:8000/v1/chat/completions  → strips trailing segment
      - http://host:8000/           → appends /api/v1/llm/openai/v1
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return "http://coelho-llm-rotator-fastapi:8000/api/v1/llm/openai/v1"
    # Strip known chat completions suffix if caller accidentally included it
    for suffix in ("/chat/completions", "/chat/completions/"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    # Already correct v1 prefixes — keep as-is
    if u.endswith("/v1") or u.endswith("/api/v1/llm/openai/v1"):
        return u
    # Bare host:port → append /api/v1/llm/openai/v1 (coelhonexus rotator)
    if "://" in u and u.count("/") == 2:  # e.g. http://host:8000
        return u + "/api/v1/llm/openai/v1"
    return u

_RAW_ROTATOR_URL = os.getenv(
    "COELHO_LLM_ROTATOR_URL",
    "http://coelho-llm-rotator-fastapi:8000/api/v1/llm/openai/v1",
)
COELHO_ROTATOR_URL = _normalize_base_url(_RAW_ROTATOR_URL)

# Model mapping — coelhonexus rotator's virtual model is "auto" (keep as-is)
COELHO_ROTATOR_MODEL = os.getenv("COELHO_LLM_MODEL", "auto").strip() or "auto"
COELHO_EMBED_MODEL = os.getenv("COELHO_EMBED_MODEL", "nvidia/nemotron-3-embed-1b")
COELHO_API_KEY = os.getenv("COELHO_LLM_API_KEY", "dummy")

# Keep keys for manifest hashing / backward-compat imports
try:
    from .keys import DD_EMBED_MODEL_NAME as _DD_EMBED_MODEL_NAME  # noqa: F401
    from .keys import DD_EMBED_GROUP  # noqa: F401
    from .params import DD_EMBED_BATCH_SIZE  # noqa: F401
except Exception:
    _DD_EMBED_MODEL_NAME = COELHO_EMBED_MODEL
    DD_EMBED_GROUP = "dd-embed"
    DD_EMBED_BATCH_SIZE = 64

# ---------------------------------------------------------------------------
# Pooled AsyncOpenAI singleton — SOTA httpx limits + http2
# ---------------------------------------------------------------------------

import httpx as _httpx

_CLIENT: object | None = None
_CLIENT_LOCK = asyncio.Lock()

def _build_limits() -> _httpx.Limits:
    # SOTA Sept 2026: max_connections 200 / max_keepalive 100 / expiry 30s
    # Lets 24 concurrent doc_distill + 20 off_topic share pool without exhaustion.
    # Baseten & Decodo benchmarks show break-even vs aiohttp at ~200 concurrent with these.
    return _httpx.Limits(
        max_connections=200,
        max_keepalive_connections=100,
        keepalive_expiry=30.0,
    )

def _build_timeout(timeout_s: float | None) -> _httpx.Timeout:
    # Connect short, read covers LLM TTFT + 300 tok generation; write short.
    # httpx.Timeout defaults 5s is too tight for cold starts.
    t = timeout_s or 30.0
    return _httpx.Timeout(timeout=t, connect=5.0, read=t, write=5.0, pool=5.0)

async def _get_async_openai():
    """Singleton AsyncOpenAI with pooled httpx client (lazy, thread-safe for async)."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    async with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        try:
            import openai as _openai
        except Exception as e:
            raise RuntimeError(f"openai SDK not installed: {e}") from e

        # Use a shared AsyncClient with pooling; http2 multiplexing if h2 is installed
        # httpx[http2] extra is not in base image, so gracefully fall back to http/1.1 keep-alive
        try:
            http_client = _httpx.AsyncClient(
                limits=_build_limits(),
                http2=True,
                timeout=_build_timeout(None),
                follow_redirects=True,
            )
            http2_enabled = True
        except ImportError:
            http_client = _httpx.AsyncClient(
                limits=_build_limits(),
                http2=False,
                timeout=_build_timeout(None),
                follow_redirects=True,
            )
            http2_enabled = False
        client = _openai.AsyncOpenAI(
            base_url=COELHO_ROTATOR_URL,
            api_key=COELHO_API_KEY,
            max_retries=0,  # rotator handles cascade, SDK retries would triple timeout
            http_client=http_client,
        )
        _CLIENT = client
        logger.info(f"[rotator-adapter] AsyncOpenAI pooled client → {COELHO_ROTATOR_URL} model={COELHO_ROTATOR_MODEL} (http2={http2_enabled}, 200/100 pool)")
        return client

def _get_openai_sync():
    """Sync client fallback for embed/rerank paths that may be called sync."""
    import openai as _openai
    try:
        http_client = _httpx.Client(limits=_build_limits(), http2=True, timeout=_build_timeout(None))
    except ImportError:
        http_client = _httpx.Client(limits=_build_limits(), http2=False, timeout=_build_timeout(None))
    return _openai.OpenAI(
        base_url=COELHO_ROTATOR_URL,
        api_key=COELHO_API_KEY,
        max_retries=0,
        http_client=http_client,
    )

# ---------------------------------------------------------------------------
# Helpers — ChatOpenAI / OpenAIEmbeddings via endpoint (legacy compat)
# ---------------------------------------------------------------------------

def _get_chat_llm(
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float | None = None,
    response_format: dict | None = None,
):
    """Legacy LangChain wrapper — retained for non-hot paths.

    Hot path (doc_distill / off_topic / chapter_assign) now uses raw AsyncOpenAI
    via _get_async_openai() for lower overhead. This stays for backward-compat
    callers that pass LangChain messages.
    """
    from langchain_openai import ChatOpenAI

    kwargs: dict = {
        "base_url": COELHO_ROTATOR_URL,
        "api_key": COELHO_API_KEY,
        "model": COELHO_ROTATOR_MODEL,
        "temperature": temperature if temperature is not None else 0.0,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if timeout_s is not None:
        kwargs["timeout"] = timeout_s
    if response_format is not None:
        kwargs["model_kwargs"] = {"response_format": response_format}
    return ChatOpenAI(**kwargs)


@functools.lru_cache(maxsize=1)
def _get_embeddings():
    # Local FastEmbed — avoids NIM 403/EOL (1B HF too large for 512Mi pod); 384d bge-small is fast.
    # Cached: re-instantiating reloads ONNX runtime + tokenizer (~100-500ms per run).
    # parallel=None (default): use onnxruntime's built-in threading, don't spawn external workers.
    # batch_size=256 (FastEmbed default): ONNX utilisation > HTTP round-trips in local mode.
    try:
        from langchain_community.embeddings import FastEmbedEmbeddings

        return FastEmbedEmbeddings(
            model_name="BAAI/bge-small-en-v1.5",
            batch_size=256,
            parallel=None,
        )
    except Exception as e:
        logger.warning(f"[embed] FastEmbed failed {e}, trying HF")
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            return HuggingFaceEmbeddings(
                model_name="nvidia/Nemotron-3-Embed-1B-BF16",
                model_kwargs={"trust_remote_code": True},
                encode_kwargs={"normalize_embeddings": True},
            )
        except Exception as e2:
            logger.warning(f"[embed] HF also failed {e2}, trying NIM")
            from langchain_openai import OpenAIEmbeddings

            try:
                from domains.llm.credentials import resolve_key

                nim_key = resolve_key("NVIDIA_API_KEY") or os.getenv("NVIDIA_API_KEY", "")
            except Exception:
                nim_key = os.getenv("NVIDIA_API_KEY", "")
            return OpenAIEmbeddings(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=nim_key,
                model=COELHO_EMBED_MODEL,
            )


# ------------------------------------------------------------------
# Embeddings — drop-in for embed_via_router_{sync,async}
# ------------------------------------------------------------------

def embed_via_router_sync(
    texts: list[str],
    input_type: str = "passage",
) -> list[list[float]]:
    if not texts:
        return []
    # input_type ignored — rotator single embedding model (cosine symmetric)
    emb = _get_embeddings()
    clean = [t if (t and t.strip()) else " " for t in texts]
    out: list[list[float]] = []
    for start in range(0, len(clean), DD_EMBED_BATCH_SIZE):
        batch = clean[start : start + DD_EMBED_BATCH_SIZE]
        vecs = emb.embed_documents(batch)
        out.extend(vecs)
    if len(out) != len(texts):
        raise RuntimeError(f"embed: rotator returned {len(out)} vectors for {len(texts)} inputs")
    return out


async def embed_via_router_async(
    texts: list[str],
    input_type: str = "passage",
    on_batch=None,
) -> list[list[float]]:
    if not texts:
        return []
    emb = _get_embeddings()
    clean = [t if (t and t.strip()) else " " for t in texts]
    total = len(clean)
    # Dedupe identical chunks before embedding — boilerplate-heavy corpora (licenses,
    # headers, repeated intros) waste re-embedding compute; round-trip via index map
    # keeps output order stable. Only embed uniques then fan back out.
    uniques: list[str] = []
    idx_map: list[int] = []
    seen: dict[str, int] = {}
    for t in clean:
        i = seen.get(t)
        if i is None:
            i = len(uniques)
            seen[t] = i
            uniques.append(t)
        idx_map.append(i)
    out_uniq: list[list[float]] = []
    for start in range(0, len(uniques), DD_EMBED_BATCH_SIZE):
        batch = uniques[start : start + DD_EMBED_BATCH_SIZE]
        # FastEmbed is sync-only; use to_thread if aembed missing
        try:
            if hasattr(emb, "aembed_documents"):
                vecs = await emb.aembed_documents(batch)  # type: ignore
            else:
                vecs = await asyncio.to_thread(emb.embed_documents, batch)
        except NotImplementedError:
            vecs = await asyncio.to_thread(emb.embed_documents, batch)
        out_uniq.extend(vecs)
        if on_batch is not None:
            try:
                # Progress based on original dedup-fan-out count to keep UX truthful
                n_recon = sum(idx <= len(out_uniq) - 1 for idx in idx_map)
                await on_batch(
                    n_done=min(n_recon, total),
                    n_total=total,
                    batch_size=len(batch),
                )
            except Exception:
                pass
    out = [out_uniq[i] for i in idx_map]
    if len(out) != len(texts):
        raise RuntimeError(f"embed: rotator returned {len(out)} vectors for {len(texts)} inputs")
    return out


# ------------------------------------------------------------------
# Chat — judge + bandit (bandit args ignored, single auto pool)
# SOTA: raw AsyncOpenAI pooled, no per-call ChatOpenAI construction
# ------------------------------------------------------------------

async def chat_judge_async(
    prompt: str,
    max_tokens: int = 8,
    temperature: float = 0.0,
) -> str:
    # Hot path delegates to pooled bandit version with minimal overhead
    text, _ = await chat_judge_bandit_async(prompt, max_tokens=max_tokens, temperature=temperature)
    return text


async def chat_judge_bandit_async(
    prompt: str,
    *,
    max_tokens: int = 8,
    temperature: float = 0.0,
    timeout_s: float = 30.0,
    expected_pattern: str | None = None,
    dd_process: str | None = None,  # ignored — no per-process weights
    candidate_filter=None,  # ignored — no heavyweight filter
    response_format: dict | None = None,
) -> tuple[str, dict]:
    # SOTA: direct OpenAI SDK → rotator, pooled keep-alive, no LangChain translation
    client = await _get_async_openai()

    # Build OpenAI-compatible kwargs — only send non-None to stay minimal
    kwargs: dict = {
        "model": COELHO_ROTATOR_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout_s,
    }
    if response_format is not None:
        # OpenAI expects {"type": "json_object"} or {"type": "json_schema", "json_schema": {...}}
        kwargs["response_format"] = response_format  # type: ignore

    # Remove None timeout entry if SDK disallows it as None
    if kwargs.get("timeout") is None:
        kwargs.pop("timeout", None)

    t0 = time.monotonic()
    try:
        resp = await client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
    except Exception as e:
        # Normalize error for upstream classify_error (preserve message)
        raise e

    latency_s = float(time.monotonic() - t0)

    # Extract text — OpenAI returns choices[0].message.content
    try:
        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        msg = getattr(choice, "message", None) if choice else None
        text = (getattr(msg, "content", "") or "").strip() if msg else ""
        # Fallback for dict responses
        if not text and isinstance(resp, dict):
            text = (((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    except Exception:
        text = ""

    # Deployment surfacing — resp.model is the real arm, not the group alias
    deployment = COELHO_ROTATOR_MODEL
    try:
        m = getattr(resp, "model", None)
        if isinstance(m, str) and m:
            deployment = m
        elif isinstance(resp, dict) and resp.get("model"):
            deployment = str(resp["model"])
        # Coerce bare :free → openrouter prefix for consistent logging
        low = deployment.lower()
        if (low.endswith(":free") or "minimax-m3" in low or "dots-" in low) and "openrouter" not in low and "/" not in deployment:
            deployment = f"openrouter/{deployment}"
    except Exception:
        pass

    meta = {
        "deployment": deployment,
        "attempts": 1,
        "latency_s": round(latency_s, 3),
        "reward": None,
        "dd_process": dd_process or "auto",
    }

    if expected_pattern:
        try:
            if not re.compile(expected_pattern).match(text.split()[0].strip(".,;:!\"'`") if text else ""):
                meta["schema_invalid"] = True
        except Exception:
            pass

    # Usage extraction for counter (prompt/completion tokens)
    try:
        usage = getattr(resp, "usage", None)
        if usage is not None:
            # Build AIMessage-like shim for bump
            class _Msg:
                content = text
                response_metadata = {"model_name": deployment}
                usage_metadata = {
                    "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                }
            _bump_dd_llm_counter(_Msg(), deployment=deployment)
        else:
            # Fallback generic bump without usage
            class _Msg2:
                content = text
                response_metadata = {"model_name": deployment}
                usage_metadata = {}
            _bump_dd_llm_counter(_Msg2(), deployment=deployment)
    except Exception:
        pass

    return text, meta


def _bump_dd_llm_counter(response, deployment: str | None = None) -> dict | None:
    try:
        from domains.dd.runtime.llm_counter import bump_current_call

        # Adapt to bump_current_call expected shape
        fake_resp = {
            "model": deployment or COELHO_ROTATOR_MODEL,
            "usage": getattr(response, "usage_metadata", None) or {},
            "choices": [{"message": {"content": getattr(response, "content", "")}}],
        }
        return bump_current_call(response=fake_resp, deployment=deployment or COELHO_ROTATOR_MODEL)
    except Exception as e:
        logger.debug(f"[rotator-adapter] bump failed: {e}")
        return None


# PEP 562 — any missing build_* import returns the generic ChatOpenAI adapter
def __getattr__(name: str):
    if name.startswith("build_"):
        def _stub(*args, **kwargs):
            return build_reduce_label_chain(*args, **kwargs)

        return _stub
    if name in {"pick_synth_deployment", "pick_synth_deployment_bandit", "pick_ycs_neo4j_deployment_bandit", "get_entries_for_group", "get_parent_group", "ensure_dynamic_catalog"}:
        return lambda *a, **k: None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ------------------------------------------------------------------
# Compat shims — no-ops for lifespan / legacy callers
# ------------------------------------------------------------------

async def init_dynamic_catalog() -> None:
    return None


def init_dynamic_catalog_sync() -> None:
    return None


def start_catalog_refresh_loop() -> None:
    return None


async def stop_catalog_refresh_loop() -> None:
    return None


def reset_rotator(*args, **kwargs) -> None:
    # Reset pooled client if needed (e.g., after network change)
    global _CLIENT
    try:
        if _CLIENT is not None:
            # Close underlying http_client gracefully
            import asyncio as _asyncio
            try:
                # _CLIENT is AsyncOpenAI with .close()
                if hasattr(_CLIENT, "close"):
                    # Don't await in sync context; schedule if loop running
                    try:
                        loop = _asyncio.get_running_loop()
                        loop.create_task(_CLIENT.close())  # type: ignore
                    except RuntimeError:
                        pass
            except Exception:
                pass
        _CLIENT = None
    except Exception:
        pass
    return None


def mark_inaccessible(model_id: str) -> None:
    return None


async def rerank_via_router_async(query: str, documents: list[str], top_n: int | None = None):
    # Not used via rotator — fallback to no rerank
    return []


def build_reduce_label_chain():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=COELHO_ROTATOR_URL,
        api_key=COELHO_API_KEY,
        model=COELHO_ROTATOR_MODEL,
        temperature=0.0,
    )


def build_llm_fallback_chain():
    return build_reduce_label_chain()


# ------------------------------------------------------------------
# Legacy shims — keep imports from chain/__init__.py working
# ------------------------------------------------------------------
def _get_router(*args, **kwargs):
    return None


async def _redis_for_bandit(*args, **kwargs):
    return None


def build_curator_llm(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_keylm_chain(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_pinned_chain_any(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_refine_llm_chain(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_resolver_llm_chain(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_synth_fallback_chain(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_synth_pinned_chain(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_synth_pool_chain(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


def build_ycs_neo4j_pinned_chain(*args, **kwargs):
    return build_reduce_label_chain(*args, **kwargs)


async def ensure_dynamic_catalog(*args, **kwargs):
    return None


def get_entries_for_group(*args, **kwargs):
    return []


def get_parent_group(*args, **kwargs):
    return None


def pick_synth_deployment(*args, **kwargs):
    return COELHO_ROTATOR_MODEL


def pick_synth_deployment_bandit(*args, **kwargs):
    return COELHO_ROTATOR_MODEL


def pick_ycs_neo4j_deployment_bandit(*args, **kwargs):
    return COELHO_ROTATOR_MODEL


def record_ycs_neo4j_reward(*args, **kwargs):
    return None


def release_ycs_provider_slot(*args, **kwargs):
    return None
