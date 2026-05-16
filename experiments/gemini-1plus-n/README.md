# Gemini 1+N Experiment

**Purpose:** validate whether a single long-context Gemini 2.5 Flash call + N parallel per-chapter expansion calls produces materially better docs-distilled output than the current ~4000-LoC hierarchical pipeline before committing to the hybrid pivot.

**Standalone**: this script does NOT import anything from `apps/` or `services/`. No MinIO, Celery, rotator, LangGraph, observability. Pure Gemini calls → files on disk. The architectural comparison stays clean.

## Setup

```bash
cd experiments/gemini-1plus-n
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Pick a provider (or both):

```bash
# Gemini 2.5 Flash via AI Studio (free, 1M ctx, ~20 RPD)
#   Get key: https://aistudio.google.com → Get API key
export GEMINI_API_KEY="..."

# NVIDIA NIM (DeepSeek V4 Flash, free, 1M ctx, 40 RPM)
#   Get key: https://build.nvidia.com → Get API key
export NVIDIA_API_KEY="..."
```

## Run

```bash
# Default — Gemini provider
python run.py

# NIM provider (DeepSeek V4 Flash, 40 RPM, far more iteration headroom)
python run.py --provider nim

# Other framework
python run.py --provider nim --framework Pydantic --url https://pydantic.dev/docs/validation/llms-full.txt

# Skip coherence diagnostic (~5-10s faster)
python run.py --skip-coherence

# Custom output dir
python run.py --provider nim --out ./out-litellm-nim
```

## Provider comparison

| Provider | Model | Context | Free-tier limit | When to use |
|---|---|---|---|---|
| `gemini` | `gemini-2.5-flash` | 1M | ~20 RPD per fresh account | High-quality baseline; one or two runs/day |
| `nim` | `deepseek-ai/deepseek-v4-flash` | 1M | 40 RPM, no documented RPD cap | Daily iteration without quota anxiety |

The script resumes from disk per chapter (deletes `out/outline.json` to force a fresh outline). You can mix providers across runs against the same `--out` directory if you want — chapters from earlier runs are reused.

## What it does

1. **Fetch** `llms-full.txt` (one HTTP call, ~550 KB for LiteLLM)
2. **Split** the monolith on H1/H2 boundaries → numbered sections
3. **Outline call** (Gemini 2.5 Flash, ~150K-token input): "produce N chapters; assign section IDs"
4. **Expansion calls** (parallel, sem=5): per chapter, "write README + challenges + flashcards"
5. **Coherence diagnostic** (text-embedding-004): same `mean(cos(title_emb, section_emb))` metric as v3, so you get an apples-to-apples comparison

## Output

```
out/
├── outline.json
├── chapter01/
│   ├── README.md
│   ├── challenges.md
│   └── flashcards.json
├── chapter02/
│   ├── README.md
│   ├── challenges.md
│   └── flashcards.json
├── ...
└── summary.json     ← coherence vs v3 baseline, token usage, timing
```

## Free-tier accounting

| Resource | Limit | This experiment consumes |
|---|---|---|
| Gemini 2.5 Flash RPD | 1,500 | **~11** per run (1 outline + ~10 expansions) |
| Gemini 2.5 Flash RPM | 15 | peak ~5 (sem-capped) |
| text-embedding-004 RPD | 1,500 | **1** per run (batched embed) |
| TPM | 1,000,000 | ~150K per outline call |
| Daily studies | — | ~130 full runs/day before hitting RPD |

For development iteration you have effectively unlimited headroom.

## Read-out

The final console line prints a verdict template based on the coherence delta vs the v3 baseline:

- **mean coherence ≥ v3 + 0.05 AND RED count ≤ v3** → pivot is justified
- **otherwise** → current architecture is justified by quality; keep iterating §2.2 fixes

Eyeball the chapter READMEs too — numbers don't capture pedagogical fluency.

## v3 baseline (LiteLLM)

| Metric | Value |
|---|---|
| Mean coherence | 0.388 |
| RED chapters (< 0.35) | 1 |
| YEL chapters (0.35–0.50) | 6 |
| GREEN chapters (≥ 0.50) | 0 |
| n chapters | 8 |
| Wall-clock | 187 s (planner only) |

If Gemini one-shot beats this materially, commit to the hybrid pivot.
