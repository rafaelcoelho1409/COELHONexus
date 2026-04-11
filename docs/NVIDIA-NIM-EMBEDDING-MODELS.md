# NVIDIA NIM Embedding Models — Test Results

> Tested on 2026-04-11 with COELHONexus API key.
> All models use the same endpoint: `https://integrate.api.nvidia.com/v1/embeddings`

## Working Models

| Model | Dims | Max Tokens | Status | Notes |
|-------|:----:|:----------:|:------:|-------|
| `nvidia/llama-nemotron-embed-1b-v2` | 2048 | 8192 | **Works** | Latest NVIDIA, best quality in 2048d group |
| `nvidia/llama-3.2-nv-embedqa-1b-v2` | 2048 | 8192 | **Works** | Multilingual (26 languages) |
| `nvidia/llama-nemotron-embed-vl-1b-v2` | 2048 | 8192 | **Works** | Vision + language capable |
| `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1` | 2048 | 8192 | **Works** | Multimodal retrieval |
| `nvidia/llama-3.2-nemoretriever-300m-embed-v1` | 2048 | 8192 | **Works** | Lightweight (300M params) |
| `nvidia/nv-embedqa-e5-v5` | 1024 | 512 | **Works** | E5-large fine-tune, mature |
| `nvidia/nv-embed-v1` | 4096 | 512 | **Works** | Highest quality (MTEB 69.32) |
| `nvidia/nv-embedcode-7b-v1` | 4096 | 512 | **Works** | Code-specific embeddings |

## Unavailable Models (account-level restriction)

| Model | Error |
|-------|-------|
| `nvidia/nv-embedqa-mistral-7b-v2` | Not found for account (MTEB #1: 72.31 — best quality overall) |
| `nvidia/embed-qa-4` | Not found for account |
| `nvidia/llama-3.2-nv-embedqa-1b-v1` | Not found for account (older v1) |
| `snowflake/arctic-embed-l` | Not found for account |

## Broken Models

| Model | Error |
|-------|-------|
| `baai/bge-m3` | "Something went wrong with the request" (all input formats tested) |

## Dimension Groups (for Qdrant collection compatibility)

Vectors from different models are **incompatible** — cannot mix in the same Qdrant collection.

| Dimension | Models Available | Best Model |
|:---------:|:---:|-----------|
| **2048d** | 5 models | `nvidia/llama-nemotron-embed-1b-v2` |
| 4096d | 2 models | `nvidia/nv-embed-v1` |
| 1024d | 1 model | `nvidia/nv-embedqa-e5-v5` |

## Rate Limits

- ~40 RPM per model (per-model, not per-account)
- No daily token limit on free tier
- Retry with exponential backoff on 429

## Configuration

Default model is set in `services/embeddings.py` via `NVIDIA_EMBEDDING_MODEL` environment variable.
Override by setting the env var in Helm values or `.env` file.

---

*Last tested: 2026-04-11*
