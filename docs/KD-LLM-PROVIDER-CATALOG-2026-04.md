# KD LLM Provider Catalog — Deep Research (2026-04-23)

Comprehensive survey of free-tier LLM API providers for expanding the
`services/llm_chain.py` fallback chain. Compiled while NVIDIA NIM was
experiencing documented degradation (glm-5.1 hanging, 504 gateway
timeouts — see NVIDIA forum threads in Sources).

**Re-verify quarterly.** Free-tier terms, model availability, and rate
limits change often. Dated header above is the snapshot date.

---

## TL;DR — the short list

| Add to chain | Why | Priority |
|---|---|---|
| **Cerebras** | Same `gpt-oss-120b` we already trust on Groq, but 1M tokens/day, 30 RPM, 3000 tok/s. Best single addition. Drop-in top-of-chain replacement when NIM is degraded. | **1 — ship first** |
| **Mistral La Plateforme** | 1 **billion** tokens/month free (largest generous free tier of any provider). Mistral Large + Codestral. Native function calling + structured output. | 2 |
| **Google Gemini 2.5 Flash** | 1500 RPD free; `gemini-2.5-flash` is "king of free tier 2026" per multiple 2026 comparisons. Native `with_structured_output` in LangChain. | 3 |
| **Zhipu GLM-4.7-Flash + GLM-4.5-Flash** | Zero-cost, no daily quota. 25M token signup bonus. Chinese datacenter = +latency from LATAM/US. | 4 (if latency acceptable) |
| **DeepSeek API** | 5M tokens free on signup (30 days). **No formal rate limit.** Native tool-calling on `deepseek-chat` + `deepseek-reasoner`. | 5 |
| **SambaNova** | 10-30 RPM by model. Llama 3.3 70B with function calling, 400 tok/s on RDU. $5 signup credit, then free-rate-limited. OpenAI-compat with minor quirks. | 6 |
| **OpenRouter `:free` models** | 29 free models via one API. 50 RPD free / 1000 RPD with $10 topped up. Useful for exotic models not in main providers. | 7 (optional) |

**Skip:** Anthropic ($5 trial only), Fireworks ($1 trial), DeepInfra ($5
trial), xAI Grok (no true free API), Perplexity (no free credits),
Cohere (non-commercial only), Hugging Face Inference (tight quotas,
model-limited), Nebius / Baseten / Lepton (paid-only).

---

## Already integrated in `services/llm_chain.py`

### Groq

- **Catalog**: `meta-llama/llama-4-scout-17b-16e-instruct` (512K ctx), `openai/gpt-oss-120b`, `qwen/qwen3-32b`, `llama-3.3-70b-versatile`, `openai/gpt-oss-20b`, `llama-3.1-8b-instant`
- **Free tier**: ~6K TPM cap on most models (Run-6: 172 rate-limit retries on MAP stampede pre-Tier-1 #4 sem)
- **Pros**: 300+ tok/s (LPU hardware), best function-calling reliability on `llama-3.3-70b-versatile`
- **Cons**: TPM limit constrains large prompts. `openai/gpt-oss-20b` requires org-level + project-level allowlist (discovered Run-6)
- **Status**: Healthy, restrictive TPM

### NVIDIA NIM

- **Catalog**: `z-ai/glm-5.1`, `qwen/qwen3.5-397b-a17b`, `moonshotai/kimi-k2.5`, `z-ai/glm4.7`, `nvidia/nemotron-3-super-120b-a12b` (1M ctx), `deepseek-ai/deepseek-v3.2`, `minimaxai/minimax-m2.7`, `mistralai/mistral-large-3-675b-instruct-2512`
- **Free tier**: 40 RPM per model
- **Function calling**: Yes, with documented malformed-JSON tool_call quirk (see `services/llm_chain.py` header)
- **Status 2026-04-23**: **DEGRADED**. Forum threads 366913 (April 17-18) + 367453 (April 22-23) confirm widespread 504s and glm-5.1 hanging. Not a code issue on our side.

---

## Recommended additions — detail cards

Priority-ordered. All support function calling / structured output unless
noted.

### 1. Cerebras Inference

- **URL**: `https://api.cerebras.ai/v1/chat/completions`
- **LangChain package**: `langchain-cerebras` (native) OR `ChatOpenAI(base_url=...)` — OpenAI-compat confirmed
- **Env var**: `CEREBRAS_API_KEY`
- **Pricing model**: Free tier, no credit card required
- **Rate limits (free tier)**:

  | Model | RPM | TPM | TPD |
  |---|---|---|---|
  | `gpt-oss-120b` | 30 | 64K | **1M** |
  | `qwen-3-235b-a22b-instruct-2507` | 30 | 60K | **1M** |
  | `llama3.1-8b` | 30 | 60K | **1M** |
  | `zai-glm-4.7` | 10 | 60K | **1M** |

- **Function calling**: **Yes, native**. gpt-oss-120b natively supports
  function calling, structured outputs, web browsing, Python code
  execution. zai-glm-4.7 also documented as tool-capable.
- **Throughput**: up to **3000 tok/s** on `gpt-oss-120b`
- **Context window**: 131K (gpt-oss-120b), 262K (qwen-3-235b)
- **Why this is the best single addition**: same model
  (`openai/gpt-oss-120b`) as our Groq entry, but Cerebras runs it ~10x
  faster and has 1M TPD instead of Groq's 6K TPM. When NIM glm-5.1
  hangs, this serves in ~0.1s. No catalog-drift risk — gpt-oss-120b is
  a stable OpenAI open-weights model.

### 2. Mistral La Plateforme

- **URL**: `https://api.mistral.ai/v1/chat/completions`
- **LangChain package**: `langchain-mistralai` (native)
- **Env var**: `MISTRAL_API_KEY`
- **Pricing model**: Free "Experiment" tier, no credit card
- **Rate limits (free tier)**:
  - **1 billion tokens/month** (largest generous quota of any provider)
- **Models available**: Mistral Large (flagship), Codestral (code-focused),
  Mistral Small, Mistral Nemo
- **Function calling**: **Yes, native**. JSON mode + structured output
  (response_format) both supported
- **Context window**: up to 128K on Mistral Large
- **Why**: A billion tokens per month is ~33M/day — dwarfs every other
  free tier. If our synth chain averages ~200K tokens per study run,
  that's 165 studies/month of pure Mistral coverage as a backup. Also:
  Mistral Large is a frontier-class European model; diversifies us
  away from US + China concentration.

### 3. Google Gemini (AI Studio)

- **URL**: `https://generativelanguage.googleapis.com/v1beta/...`
- **LangChain package**: `langchain-google-genai` (native; uses `ChatGoogleGenerativeAI`)
- **Env var**: `GOOGLE_API_KEY` (from aistudio.google.com)
- **Pricing model**: Free tier, no credit card
- **Rate limits (free tier, Gemini 2.5 Flash)**:
  - **1500 RPD** free (most-cited figure in 2026 reviews)
  - TPM and exact per-model RPM depend on account tier — view at
    `aistudio.google.com/rate-limit`
- **Models on free tier**: `gemini-2.5-flash` (frontier), `gemini-2.0-flash`, `gemini-2.0-flash-lite`, Gemma family
- **Models paywalled since 2026-04-01**: `gemini-2.5-pro` (free access restricted)
- **Function calling**: **Yes, native**. LangChain `with_structured_output()`
  supported directly on `ChatGoogleGenerativeAI`. Three implementation
  paths: JSON schema, Python functions with auto-schema, OpenAI-compat API.
- **Context window**: 1M tokens (Gemini 2.5 Flash)
- **Why**: 1500 RPD = ~62 requests/hour sustained. For homelab
  study-generation pace (1-2 studies/day × 50-150 LLM calls/study) this
  is plenty. Gemini 2.5 Flash benchmarks in the frontier class for
  reasoning. Known good structured-output behavior in LangChain.
- **Caveat**: policy changed 2026-04-01 — mandatory spending caps + Pro
  paywalled. Free Flash tier still generous but watch for further tightening.

### 4. Zhipu GLM (`z-ai` / bigmodel.cn)

- **URL**: `https://open.bigmodel.cn/api/paas/v4/chat/completions`
- **LangChain package**: `ChatOpenAI(base_url=...)` (OpenAI-compat)
- **Env var**: `ZHIPU_API_KEY`
- **Pricing model**: Two models are **free, zero cost, no daily cap**:
  - `GLM-4.7-Flash`
  - `GLM-4.5-Flash`
- **Bonus**: 25M token signup credit
- **Paid models**: GLM-5.1, GLM-5-Turbo (2-3x rate during peak hours)
- **Function calling**: Yes, OpenAI-compat tool_calls supported
- **Latency caveat**: **Chinese data centers**. +200-500ms RTT from
  Americas/Europe vs US-based providers. Still well under our 120s
  eager timeout, but a factor for user-facing latency-sensitive apps.
- **Why**: Two truly free models with no daily quota. Excellent as
  tail-of-chain resilience — always available when everything else
  hits rate limits.

### 5. DeepSeek API

- **URL**: `https://api.deepseek.com/v1/chat/completions`
- **LangChain package**: `langchain-deepseek` (native) OR `ChatOpenAI(base_url=...)`
- **Env var**: `DEEPSEEK_API_KEY`
- **Pricing model**: **5M tokens free on signup** (30-day expiry, ~$8.40 value), no credit card
- **Rate limits**: **No formal rate limit** — DeepSeek API does not
  constrain user rate. They serve every request they can.
- **Models on free credits**: `deepseek-chat` (V3-based), `deepseek-reasoner` (R1-based)
- **Function calling**: **Yes**, both models support tool_calls
- **Context window**: 128K (V3), output capped 8K (chat) / 64K (reasoner)
- **Why**: No-rate-limit stance is unique — every other provider throttles.
  Caveat: free credits expire after 30 days; sustained use requires paid
  top-ups (pricing still cheapest among frontier models, ~$0.14/1M
  input).
- **Suggested role**: burst-capacity fallback. When primaries are
  rate-limited, DeepSeek absorbs anything you throw at it.

### 6. SambaNova Cloud

- **URL**: `https://api.sambanova.ai/v1/chat/completions`
- **LangChain package**: `ChatOpenAI(base_url=...)` (OpenAI-compat, with minor ChatMessage validation quirks) OR community `langchain-sambanova`
- **Env var**: `SAMBANOVA_API_KEY`
- **Pricing model**: $5 signup credit (~30-day expiry), then rate-limited free tier
- **Rate limits (free tier)**:

  | Model | RPM |
  |---|---|
  | Llama 3.1 8B | 30 |
  | Llama 3.1 70B / 3.3 70B | 20 |
  | Llama 3.1 405B | 10 |
  | Llama 3.2 1B / 3B | 30 |
  | Llama 3.2 11B | 10 |
  | Llama 3.2 90B | 1 (temporarily throttled) |

- **Function calling**: **Yes**, `Meta-Llama-3.3-70B-Instruct` well-tested
  on SambaNova RDU hardware (400 tok/s)
- **Context window**: 4K input on Llama 3.3 70B (smaller than peers — watch for ch02-class corpus issues)
- **Why**: Supplement for Llama-family coverage. Fast RDU inference.
  OpenAI-compatible so minimal integration code.
- **Caveat**: a thread on SambaNova's forum reports `ChatMessage` role
  validation errors when using `ChatOpenAI(base_url=...)` directly —
  may need the `langchain-sambanova` community package.

### 7. OpenRouter `:free` models

- **URL**: `https://openrouter.ai/api/v1/chat/completions`
- **LangChain package**: `ChatOpenAI(base_url=...)` (OpenAI-compat by design)
- **Env var**: `OPENROUTER_API_KEY`
- **Pricing model**:
  - 50 RPD across all free models without credits
  - **1000 RPD** with a one-time $10 credit purchase (credits are
    non-expiring; free model calls never consume them)
  - 20 RPM ceiling regardless
- **Function calling**: Supported on models flagged "Yes" in table below;
  use `:exacto` suffix for tool-calling-reliability-tuned routing
- **Free models with tool-calling support** (29 total, April 2026; 20 support tools):

  | Model | Context | Notes |
  |---|---|---|
  | `nvidia/nemotron-3-super-120b-a12b:free` | 262K | Largest ctx, tool-capable |
  | `openai/gpt-oss-120b:free` | 131K | Same model as Cerebras/Groq |
  | `openai/gpt-oss-20b:free` | 131K | Smaller fallback |
  | `z-ai/glm-4.5-air:free` | 131K | GLM-family |
  | `qwen/qwen3-next-80b-a3b-instruct:free` | 262K | Qwen newest |
  | `qwen/qwen3-coder:free` | 262K | Code-specialized |
  | `google/gemma-4-26b-a4b-it:free` | 262K | Gemma family |
  | `google/gemma-4-31b-it:free` | 262K | Larger Gemma |
  | `tencent/hy3-preview:free` | 262K | Tencent HY |
  | `inclusionai/ling-2.6-flash:free` | 262K | — |
  | `minimax/minimax-m2.5:free` | 197K | MiniMax |
  | `meta-llama/llama-3.3-70b-instruct:free` | 66K | — |
  | `nvidia/nemotron-nano-12b-v2-vl:free` | 128K | vision+language |
  | `nvidia/nemotron-nano-9b-v2:free` | 128K | tiny |
  | `nvidia/nemotron-3-nano-30b-a3b:free` | 256K | tiny-ctx pair |
  | `openrouter/free` | 200K | generic free routing |

- **Why optional**: Aggregation layer — gives access to models we can't
  individually key into (Tencent, MiniMax, InclusionAI). Useful for
  experimentation + tail-of-chain diversity. Not essential if we have
  native integrations with Cerebras + Mistral + Gemini + Zhipu + DeepSeek
  — those cover 80% of the frontier.

---

## Explicitly skipped (why each isn't useful for our fallback chain)

| Provider | Reason skipped |
|---|---|
| **Anthropic** | Only $5 trial credit after phone verification. No ongoing free tier. Claude quality is excellent but cost/call at normal rates is too high for bulk synth. (Would reconsider if you're specifically using Claude Citations API for #22.) |
| **Fireworks AI** | $1 trial credit + 10 RPM free ceiling. Too restrictive to serve as a fallback. |
| **DeepInfra** | $5 trial credit on signup. Paid-only after. |
| **xAI Grok** | No true free API. Chat UI has "10 prompts per 2 hours" cap but API access requires paid tier (no public free tier). |
| **Perplexity** | Free plan gets **zero** API credits. Must purchase credits to use API at all. Pro sub gets $5/mo — still not really "free." |
| **Cohere** | Trial key gives 1000 calls/month, 20 RPM chat, but **non-commercial-use-only**. Can't use for customer-facing KD output. |
| **Hugging Face Inference Providers** | 100K monthly credits ($0 equiv), model-limited (~10B params on free), compute-time billing after. Cold starts common. Too flaky for hot-path fallback. |
| **Nebius AI Studio** | No free tier; very cheap paid ($0.03/1M on Llama 3.1 8B). Worth considering if we graduate to paid tier. |
| **Baseten / Lepton AI / Replicate** | No free tiers for text generation at meaningful scale. |
| **Together AI** | No free tier. Minimum $5 credit purchase to start. |

---

## Implementation plan (if you decide to expand the chain)

### Option A — Minimum-viable expansion: Cerebras only (~40 LoC)

Solves the immediate NIM-degradation problem. Drop-in replacement model
we already trust.

```python
# services/llm_chain.py — add to top-level factory section
def _cerebras(model: str, timeout_s: int) -> ChatOpenAI:
    return ChatOpenAI(
        model = model,
        temperature = 0.0,
        base_url = "https://api.cerebras.ai/v1",
        api_key = os.environ.get("CEREBRAS_API_KEY", ""),
        max_retries = 0,
        timeout = timeout_s,
    )

# Catalog — add Cerebras entries
CEREBRAS_CATALOG: list[tuple[str, str]] = [
    ("gpt-oss-120b", "cerebras-flag"),      # 3000 tok/s, 1M TPD, 30 RPM
    ("qwen-3-235b-a22b-instruct-2507", "cerebras-big"),  # 262K ctx
    ("llama3.1-8b", "cerebras-fast"),       # speed fallback
]

# In _ordered_fallback(), weave in:
c = {tag: _cerebras(m, nim_timeout_s) for m, tag in CEREBRAS_CATALOG}
# Insert c["cerebras-flag"] near the top (above nim["nim-top"] during NIM degradation)
```

Add to `pyproject.toml`:
```toml
"langchain-cerebras",   # optional, can use ChatOpenAI(base_url=...) instead
```

Add to env (k8s ConfigMap or .env):
```
CEREBRAS_API_KEY=<signup at cerebras.ai>
```

### Option B — Full diversification: Cerebras + Mistral + Gemini (~180 LoC)

Builds Option A plus:

```python
# Mistral via native package
from langchain_mistralai import ChatMistralAI
def _mistral(model: str, timeout_s: int) -> ChatMistralAI:
    return ChatMistralAI(
        model = model,
        temperature = 0.0,
        api_key = os.environ.get("MISTRAL_API_KEY", ""),
        max_retries = 0,
        timeout = timeout_s,
    )

# Gemini via native package
from langchain_google_genai import ChatGoogleGenerativeAI
def _gemini(model: str, timeout_s: int) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model = model,
        temperature = 0.0,
        google_api_key = os.environ.get("GOOGLE_API_KEY", ""),
        max_retries = 0,
        timeout = timeout_s,
    )

MISTRAL_CATALOG = [
    ("mistral-large-latest", "mistral-flag"),
    ("codestral-latest", "mistral-code"),
    ("mistral-small-latest", "mistral-fast"),
]

GEMINI_CATALOG = [
    ("gemini-2.5-flash", "gemini-flash"),  # frontier on free tier
    ("gemini-2.0-flash", "gemini-flash-prev"),
]
```

Add to `pyproject.toml`:
```toml
"langchain-mistralai",
"langchain-google-genai",
```

New env vars: `MISTRAL_API_KEY`, `GOOGLE_API_KEY`.

### Option C — Comprehensive diversification: everything (~300 LoC)

Add Zhipu + DeepSeek + SambaNova + OpenRouter on top of B. All
OpenAI-compat so each is ~30-40 LoC (no new LangChain packages needed
beyond B's two).

Suggested chain order after C:

| Position | Model | Provider | Rationale |
|---|---|---|---|
| 1 | gpt-oss-120b | **Cerebras** | Fastest, 1M TPD, proven model |
| 2 | gemini-2.5-flash | **Google** | Frontier reasoning, 1500 RPD |
| 3 | mistral-large-latest | **Mistral** | Frontier, 1B tok/month |
| 4 | glm-5.1 | **NIM** | (keep; flaky as of 2026-04 but premium when healthy) |
| 5 | qwen3.5-397b-a17b | **NIM** | 262K ctx, structured output |
| 6 | deepseek-reasoner | **DeepSeek** | No rate limit, 5M free |
| 7 | qwen-3-235b-a22b | **Cerebras** | Backup large ctx |
| 8 | kimi-k2.5 | **NIM** | 262K ctx |
| 9 | Llama 3.3 70B | **SambaNova** | 400 tok/s, 20 RPM |
| 10 | glm-4.7 | **NIM** | — |
| 11 | deepseek-v3.2 | **NIM** | — |
| 12 | minimax-m2.7 | **NIM** | 204K ctx, agentic |
| 13 | nemotron-3-super-120b | **NIM** | 1M ctx insurance |
| 14 | mistral-small-latest | **Mistral** | Speed backup |
| 15 | codestral-latest | **Mistral** | Code-heavy |
| 16 | llama-4-scout-17b | **Groq** | 512K ctx, 30K TPM |
| 17 | gpt-oss-120b | **Groq** | Redundancy on same model |
| 18 | glm-4.7-flash | **Zhipu** | Free zero-cap tail |
| 19 | qwen3-32b | **Groq** | Tool-calling reliable |
| 20 | mistral-large-3-675b | **NIM** | — |
| 21 | llama3.1-8b | **Cerebras** | Speed tail |
| 22 | glm-4.5-flash | **Zhipu** | Free zero-cap tail |
| 23 | gpt-oss-20b | **Groq** | (needs project-level allow per Run-6) |
| 24 | llama-3.1-8b-instant | **Groq** | Final speed fallback |

24 models across 7 providers. No single-provider outage kills the chain.

---

## Reliability + function-calling matrix

Critical for our `with_structured_output(schema, method="function_calling")`
usage. "Reliable" here means real-world behavior, not marketing:

| Provider | Function calling | Structured output via LangChain | Observed reliability in 2026 |
|---|---|---|---|
| Cerebras | Native (confirmed on gpt-oss-120b + glm-4.7) | OpenAI-compat works | No documented quality issues |
| Mistral | Native + JSON mode | `langchain-mistralai` with_structured_output supported | Stable provider |
| Google Gemini | Native + forced calling | `ChatGoogleGenerativeAI.with_structured_output` — known issue #330 with "forced function calling" on gemini-flash in early versions; fixed in recent releases | Stable; API contract changes occasionally |
| Zhipu GLM | OpenAI-compat tool_calls | Works via `ChatOpenAI(base_url=...)` | Less tested from LATAM; latency +200ms |
| DeepSeek | Native on both chat + reasoner | `langchain-deepseek` or OpenAI-compat | Unique "no rate limit" stance; historically reliable |
| SambaNova | Native; OpenAI-compat | Community report of `ChatMessage` role validation errors with direct `ChatOpenAI(base_url=...)` | Minor compat quirks; use community package to avoid |
| OpenRouter `:free` | 20 of 29 models support tools; use `:exacto` suffix for quality | OpenAI-compat natively | Depends on underlying provider; wrapper adds ~20ms |
| NIM (current) | Native with documented malformed-JSON quirk | Works but fallback-cascades occasionally | **Degraded April 2026 per forum reports** |
| Groq (current) | Native | `llama-3.3-70b-versatile` most reliable; `gpt-oss-*` blocked at org/project level (needs manual allow) | Healthy; TPM ceiling restrictive |

---

## Cost-of-usage estimate (if all free tiers tapped)

Assuming 150 LLM calls/study, ~2000 tokens avg per call, 1 study/day:

| Metric | Per day | Per month |
|---|---|---|
| Total LLM calls | 150 | ~4500 |
| Total tokens | 300K | ~9M |
| Calls that could be served by... | | |
| — Cerebras free (30 RPM × 1 worker) | unlimited up to 1M TPD | up to 30M TPM × 24h = plenty |
| — Mistral free (1B tok/month) | any amount ≤ 33M tokens/day | 1B — covers ~100 studies |
| — Gemini 2.5 Flash (1500 RPD) | 1500 | ~45K |
| — Zhipu Flash (zero cap) | any | any |
| — DeepSeek (no rate limit, 5M tok/30d) | ~165K tok/day average | 5M signup / 30d |

**Net**: at 1 study/day we are nowhere near saturating any single free
tier on its own, let alone the combination. Sustaining 10 studies/day
still fits comfortably under Mistral's 1B monthly alone.

---

## Sources

- [Cerebras Inference — Rate Limits (2026)](https://inference-docs.cerebras.ai/support/rate-limits)
- [Cerebras gpt-oss-120b tool calling](https://developers.openai.com/cookbook/articles/gpt-oss/build-your-own-fact-checker-cerebras)
- [Mistral La Plateforme pricing / rate limits](https://docs.mistral.ai/deployment/ai-studio/tier)
- [Google Gemini API rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Google Gemini function calling in LangChain](https://www.philschmid.de/gemini-langchain-cheatsheet)
- [SambaNova function calling + JSON mode](https://docs.sambanova.ai/cloud/docs/capabilities/function-calling)
- [SambaNova Cloud rate limits](https://docs.sambanova.ai/cloud/docs/get-started/rate-limits)
- [Zhipu AI GLM pricing 2026 (free flash models)](https://vibecoding.app/blog/zhipu-ai-glm-pricing-2026)
- [DeepSeek API rate limits](https://api-docs.deepseek.com/quick_start/rate_limit)
- [DeepSeek free tier 2026](https://mydeepseekapi.com/blog/deepseek-api-free-tiers)
- [OpenRouter free models list (April 2026)](https://costgoat.com/pricing/openrouter-free-models)
- [OpenRouter rate limits + `:exacto` suffix](https://openrouter.ai/docs/api/reference/limits)
- [Every free AI API in 2026 — comparison](https://awesomeagents.ai/tools/free-ai-inference-providers-2026/)
- [NIM glm-5.1 hang thread (2026-04-22 to 23)](https://forums.developer.nvidia.com/t/missing-public-api-endpoints-permission-in-personal-organization-hang-on-z-ai-glm-5-1-via-integrate-api-nvidia-com/367453)
- [NVIDIA NIM Down? thread (2026-04-17 to 18)](https://forums.developer.nvidia.com/t/nvidia-nim-down/366913)
