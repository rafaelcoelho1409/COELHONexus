# 20 Best Free NVIDIA NIM Models for OpenClaude (April 2026)

> Research snapshot: 2026-04-19
> Endpoint: `https://integrate.api.nvidia.com/v1` (OpenAI-compatible, ~40 RPM free tier)

## Mapping anchors (Claude April 2026 lineup)

| Claude model | SWE-bench Verified | SWE-bench Pro | MCP-Atlas |
|---|---|---|---|
| Opus 4.7 | 87.6% | 64.3% | 77.3% |
| Sonnet 4.6 | 79.6% | — | — |
| Haiku 4.5 | 73.3% | 39.5% | — |

## The 20 models, ranked

| Rank | Model ID (NIM) | Params | Context | Key score | Closest Claude |
|---|---|---|---|---|---|
| 1 | `minimaxai/minimax-m2.7` | 230B MoE / 10B act | 200K | 56.2% SWE-Pro / 57.0% Terminal-Bench | **Sonnet 4.6** |
| 2 | `moonshotai/kimi-k2-thinking` | 1T MoE | 256K | 84.5% GPQA / 99.1% AIME / 71.3% SWE | **Opus 4.7** (reasoning) |
| 3 | `moonshotai/kimi-k2.5` | 1T MoE | 256K | 76.8% SWE-Verified / 85% LiveCodeBench | **Opus 4.7** (coding) ⚠️ |
| 4 | `nvidia/nemotron-3-super-120b-a12b` | 120B MoE / 12B act | 128K usable (1M marketing) | RL on 10+ envs | **Sonnet 4.6** |
| 5 | `qwen/qwen3.5-397b-a17b` | 397B MoE / 17B act | 256K | 76.7% BFCL v3 | **Sonnet 4.6** ⚠️ |
| 6 | `deepseek-ai/deepseek-v3.2` | 685B sparse-attn | 164K | top reasoning, agentic | **Sonnet 4.6** |
| 7 | `deepseek-ai/deepseek-r1-0528` | 671B MoE | 128K | 49.2% SWE / 87% AIME | Sonnet 4.6 (reasoning) |
| 8 | `meta/llama-3.1-405b-instruct` | 405B | 128K | **81.1% BFCL** (best tool-use) | Sonnet 4.6 (low) |
| 9 | `qwen/qwen3-235b-a22b` | 235B MoE / 22B act | 128K | strong agent tool-call | Sonnet 4.6 (low) |
| 10 | `qwen/qwen3.6-35b-a3b` | 35B MoE / 3B act | 128K | Apr 16, 2026 release; agentic-coding | Sonnet 4.6 (small) |
| 11 | `nvidia/nemotron-cascade-2-30b-a3b` | 30B MoE / 3B act | 128K | claims to beat Qwen3.5-397B / Kimi K2.5 | Haiku 4.5 (high) |
| 12 | `nvidia/nemotron-3-nano-30b-a3b` | 30B MoE / 3B act | 1M | 2.2–3.3× faster than Qwen3-30B | **Haiku 4.5** |
| 13 | `meta/llama-4-maverick-17b-128e` | 17B active / 400B | 1M | 84.6% MMMLU | Haiku 4.5 |
| 14 | `meta/llama-3.3-70b-instruct` | 70B | 128K | 2500 t/s, ecosystem-stable | Haiku 4.5 |
| 15 | `mistralai/mistral-small-4-119b-2603` | 119B | 128K | matches GPT-OSS 120B | Haiku 4.5 |
| 16 | `mistralai/mistral-large` | undisclosed | 128K | multilingual chat + reasoning | Haiku 4.5 |
| 17 | `meta/llama-4-scout-17b-16e` | 17B active | 10M (degrades) | **2600 t/s** (fastest) | Haiku 4.5 (small) |
| 18 | `microsoft/phi-4` | mid-sized | 128K | math reasoning, edge-grade | Haiku 4.5 (small) |
| 19 | `google/gemma-4-31b-it` | 31B | 128K | frontier-for-size | Haiku 4.5 (small) ⚠️ |
| 20 | `zai-org/glm-5` | 744B MoE / 40B act | 200K | 84.1% LiveCodeBench (paper) | Sonnet 4.6 ⛔ |

## Critical flags

- **#3 Kimi K2.5 ⚠️** — User testing confirms it **hangs the gateway in compaction** with OpenCode-class tools. Single-shot only.
- **#5 Qwen 3.5 397B ⚠️** — Must set `chat_template_kwargs.enable_thinking=false` or it leaks `<think>` blocks into tool-call arguments (NVIDIA forum, Apr 14, 2026).
- **#19 Gemma 4 31B ⚠️** — Tool-call parsers broken across Ollama / vLLM / OpenCode harnesses.
- **#20 GLM-5 ⛔** — **Deprecated 2026-04-20** per NVIDIA forum thread. **GLM-5.1 is NOT yet on the NIM API** despite Z-AI's request — there is no working migration path right now. Skip until GLM-5.1 lands.

Universal caveat: the `arguments-as-dict-not-string` and `<think>`-leak bugs documented in NVIDIA's Apr 14 forum affect *every* hosted model on `integrate.api.nvidia.com/v1`. Wrap any choice with a small JSON-repair middleware.

## Bottom-line recommendation

1. **Primary: `minimaxai/minimax-m2.7`** — newest (Apr 11, 2026), 200K context, 56%+ SWE-Pro / Terminal-Bench, most JSON-stable on hosted NIM, no known compaction hangs.
2. **Backup: `qwen/qwen3.5-397b-a17b`** — strongest BFCL/tool-calling open model on NIM *with* the `enable_thinking=false` toggle.
3. **Cheap-fast 429-fallback: `nvidia/nemotron-3-nano-30b-a3b`** — 1M context, 2-3× faster than Qwen3-30B.
4. **Reasoning loops only (planning, not tool execution): `moonshotai/kimi-k2-thinking`** — Opus-class GPQA / AIME but ~25 s latency.

## Sources

- [Vellum Open Source LLM Leaderboard](https://www.vellum.ai/open-llm-leaderboard)
- [Best NVIDIA Model List for OpenClaw — Tenten AI](https://university.tenten.co/t/best-nvidia-model-list-for-openclaw-nemoclaw/2211)
- [NVIDIA NIM Free API guide — Free-LLM.com](https://free-llm.com/provider/nvidia-nim)
- [URGENT: GLM-5 Deprecation Apr 20 — NVIDIA Developer Forums](https://forums.developer.nvidia.com/t/urgent-glm-5-deprecation-april-20-2026-replacement-z-ai-glm-5-1-not-available-in-nim-api/366610)
- [Qwen3.5 Tool Calling fixed — NVIDIA Developer Forums](https://forums.developer.nvidia.com/t/qwen3-5-tool-calling-finally-fixed-possibly/366451)
- [MiniMax M2.7 — NVIDIA API Documentation](https://docs.api.nvidia.com/nim/reference/minimaxai-minimax-m2.7)
- [Gemma 4 31B-IT — NVIDIA API Documentation](https://docs.api.nvidia.com/nim/reference/google-gemma-4-31b-it)
- [Nemotron Cascade 2 30B-A3B — NVIDIA Forums](https://forums.developer.nvidia.com/t/nvidia-nemotron-cascade-2-30b-a3b-yet-another-model-to-test/364250)
- [Nemotron 3 Super Technical Report (PDF)](https://research.nvidia.com/labs/nemotron/files/NVIDIA-Nemotron-3-Super-Technical-Report.pdf)
- [Claude Opus 4.7 Benchmarks — Vellum AI](https://www.vellum.ai/blog/claude-opus-4-7-benchmarks-explained)
- [Claude API Pricing (Opus 4.7 / Sonnet 4.6 / Haiku 4.5) — BenchLM](https://benchlm.ai/blog/posts/claude-api-pricing)
- [GLM-5.1 Intelligence Analysis — Artificial Analysis](https://artificialanalysis.ai/models/glm-5-1)
- [Qwen3.6-35B-A3B Review (Apr 16, 2026 release) — dev.to](https://dev.to/czmilo/qwen36-35b-a3b-complete-review-alibabas-open-source-coding-model-that-beats-frontier-giants-4382)
