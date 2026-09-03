"""COELHO LLM Rotator adapter — exclusive endpoint, no per-process weights.

All Planner/Synth calls now route through the universal free-quota gateway:
  http://coelho-llm-rotator-fastapi.coelhonexus-dev.svc.cluster.local:8000/api/v1/llm/openai/v1
  (fallback localhost:30021 for host port-forward)

No dd_process / heavyweight / bandit weights here — FGTS-VA + TrueSkill + latency EWMA
lives inside the rotator itself (single auto pool, 46 models). This module is a thin
OpenAI-compat adapter preserving the old `chat_judge_*` / `embed_via_router_*` signatures
so Planner/Synth require zero per-file churn.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

logger = logging.getLogger(__name__)

# Endpoint — in-cluster DNS primary, host fallback via env override
COELHO_ROTATOR_URL = os.getenv(
    "COELHO_LLM_ROTATOR_URL",
    "http://coelho-llm-rotator-fastapi.coelhonexus-dev.svc.cluster.local:8000/api/v1/llm/openai/v1",
)
COELHO_ROTATOR_MODEL = os.getenv("COELHO_LLM_MODEL", "auto")
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

# ------------------------------------------------------------------
# Helpers — ChatOpenAI / OpenAIEmbeddings via endpoint
# ------------------------------------------------------------------

def _get_chat_llm(
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout_s: float | None = None,
    response_format: dict | None = None,
):
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
        # OpenAI-compat json_object / json_schema forwarded if rotator supports it
        kwargs["model_kwargs"] = {"response_format": response_format}
    return ChatOpenAI(**kwargs)


def _get_embeddings():
    # Local FastEmbed — avoids NIM 403/EOL (1B HF too large for 512Mi pod); 384d bge-small is fast
    try:
        from langchain_community.embeddings import FastEmbedEmbeddings

        return FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
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
    out: list[list[float]] = []
    for start in range(0, total, DD_EMBED_BATCH_SIZE):
        batch = clean[start : start + DD_EMBED_BATCH_SIZE]
        # FastEmbed is sync-only; use to_thread if aembed missing
        try:
            if hasattr(emb, "aembed_documents"):
                vecs = await emb.aembed_documents(batch)  # type: ignore
            else:
                vecs = await asyncio.to_thread(emb.embed_documents, batch)
        except NotImplementedError:
            vecs = await asyncio.to_thread(emb.embed_documents, batch)
        out.extend(vecs)
        if on_batch is not None:
            try:
                await on_batch(n_done=len(out), n_total=total, batch_size=len(batch))
            except Exception:
                pass
    if len(out) != len(texts):
        raise RuntimeError(f"embed: rotator returned {len(out)} vectors for {len(texts)} inputs")
    return out


# ------------------------------------------------------------------
# Chat — judge + bandit (bandit args ignored, single auto pool)
# ------------------------------------------------------------------

async def chat_judge_async(
    prompt: str,
    max_tokens: int = 8,
    temperature: float = 0.0,
) -> str:
    llm = _get_chat_llm(max_tokens=max_tokens, temperature=temperature)
    msg = await llm.ainvoke([{"role": "user", "content": prompt}])
    return (getattr(msg, "content", "") or "").strip()


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
    # All weight/process args are no-ops — endpoint does FGTS-VA+TrueSkill internally
    llm = _get_chat_llm(
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=timeout_s,
        response_format=response_format,
    )
    # response_format json_schema → try structured output if dict
    # Keep simple: let OpenAI handle response_format via model_kwargs; parse content as-is
    msg = await llm.ainvoke([{"role": "user", "content": prompt}])
    text = (getattr(msg, "content", "") or "").strip()

    # Minimal meta for bump_current_call compatibility
    meta = {
        "deployment": getattr(getattr(msg, "response_metadata", {}) or {}, "get", lambda *_: None)("model_name", None)  # type: ignore
        if isinstance(getattr(msg, "response_metadata", None), dict)
        else COELHO_ROTATOR_MODEL,
        "attempts": 1,
        "latency_s": None,
        "reward": None,
        "dd_process": dd_process or "auto",
    }
    # Try to extract model_name from response_metadata; coerce bare :free → openrouter prefix
    try:
        rm = getattr(msg, "response_metadata", None) or {}
        if isinstance(rm, dict) and rm.get("model_name"):
            raw = str(rm["model_name"])
            low = raw.lower()
            if (low.endswith(":free") or "minimax-m3" in low or "dots-" in low) and "openrouter" not in low and "/" not in raw:
                raw = f"openrouter/{raw}"
            meta["deployment"] = raw
    except Exception:
        pass

    # Optional expected_pattern soft check — don't retry, just annotate
    if expected_pattern:
        try:
            if not re.compile(expected_pattern).match(text.split()[0].strip(".,;:!\"'`") if text else ""):
                meta["schema_invalid"] = True
        except Exception:
            pass
    # Attach bump for DD counter
    try:
        _bump_dd_llm_counter(msg, deployment=meta["deployment"])
    except Exception:
        pass
    return text, meta


def _bump_dd_llm_counter(response, deployment: str | None = None) -> dict | None:
    try:
        from domains.dd.runtime.llm_counter import bump_current_call

        # Adapt AIMessage to bump_current_call expected shape
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
