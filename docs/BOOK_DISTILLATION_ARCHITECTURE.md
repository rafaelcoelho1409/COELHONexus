# BOOK DISTILLATION — Architecture & Methodology

> Purpose: automatically compress 200-300 page books into ~100-page versions preserving actionable lessons verbatim while cutting narrative padding, justifications, and repetition.
>
> Target: apply to business/self-help/technical books where extracting lessons is the primary value.
> Output aligned with `LEARNING_PROMPT.md` conventions (code-first, no padding, dense).
>
> Compiled: 2026-04-15
> Status: research complete, implementation pending

---

## Problem Statement

### What this solves
- Books I want for career growth are 200-300 pages each
- Reading 20 books = 400-600 hours = not feasible alongside paid work
- Summaries (Blinkist, Shortform) are 15-45 min rewrites losing author voice
- Need: automated, personalized, extractive-first compression preserving the author's exact phrasing on lessons while cutting everything else

### What this is NOT
- NOT classical summarization (rewrites everything, loses voice)
- NOT RAG/Q&A (no query — you want all lessons extracted)
- NOT highlight-based (requires manual reading first)
- NOT third-party subscription (generic, opinionated, not your format)

### Requirements
1. Preserve author's exact phrasing on lessons (extractive, not abstractive)
2. Drop narrative filler, justifications, repetition ruthlessly
3. Output in LEARNING_PROMPT-aligned format: `summary.md` + `chapter01-N.md` + auxiliary views
4. Token-efficient: ~$1/book or free on local infra
5. Personalizable: classifier tunable for my career lens (AI/MLOps/LLMOps focus)
6. Repeatable: same book can be re-compressed with different filters

---

## State-of-the-Art Research (2024-2026)

### Most relevant papers

**BOOOOKSCORE** — ICLR 2024, Kim et al.
- First systematic study of book-length LLM summarization
- Claude 2 with 88K chunk + incremental updating = best coherence (90.9 BOOOOKSCORE)
- Identified **8 coherence error types**: causal omissions, salience errors, repetition, entity confusion, etc.
- **Trade-off**: hierarchical merge = coherent/low-detail. Incremental = high-detail/lower-coherence.
- Key insight for our use case: we want HIGH DETAIL (preserve lessons verbatim) → incremental-bias applies

**Chain of Agents (CoA)** — NeurIPS 2024, Google
- Worker agents process chunks sequentially with "interleaved read-process"
- Manager agent synthesizes
- **Complexity**: n² → nk (dramatic scaling win)
- Up to 10% improvement over RAG and full-context
- Tested on Claude 3 (Haiku, Sonnet, Opus)

**NexusSum** — ACL 2025, Kim & Kim
- Multi-agent LLM framework for narrative summarization
- Dialogue-to-Description Transform + Hierarchical Multi-LLM
- **30% BERTScore improvement** over prior SOTA on BookSum

**Learning to Summarize from Human Feedback** — Stiennon et al., OpenAI 2020
- Foundational recursive summarization from human feedback
- Established hierarchical merging as viable for book-length

**Industry consensus 2026**: 55/45 extractive/abstractive hybrid split. Extractive-first preserves author voice; abstractive polish on kept content only.

### Where existing tools fall short

| Tool | Gap |
|---|---|
| **Blinkist** | 9000-title catalog, 15-min blinks, rewritten prose, $$ subscription |
| **Shortform** | 1000-title catalog, 45-60 min guides, rewritten in their voice, $$ |
| **Headway** | Blinkist-like, mobile-first, generic opinion |
| **NotebookLM** | Q&A over one book at a time, NOT compression output |
| **Readwise Reader + Ghostreader** | Requires you to highlight first (manual) |
| **Claude Projects / Claude.ai** | Can dump PDF + prompt, but no persistence, no repeatability, $$ per call |
| **BOOOOKSCORE tooling** | Academic evaluation tool, not a product |

**Nothing matches: automated, personalized, author-voice-preserving book compression in your format.**

---

## Architecture: Agentic Extractive-Hierarchical Distillation (AEHD)

Synthesizes Chain of Agents (parallel worker pattern) + NexusSum (multi-agent hierarchy) + BOOOOKSCORE (incremental-updating for detail preservation) + industry extractive-first trend.

### Six-stage pipeline

```
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: Parse (deterministic, no LLM)                     │
│  Book (EPUB/PDF/MOBI) → Calibre / pypdf → structured corpus │
│  Preserve: chapter boundaries, headings, footnotes,         │
│            block quotes, code blocks, tables                │
│  Output: Postgres doc_corpus table                          │
│  Cost: $0, deterministic                                    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  STAGE 2: Parallel Multi-Dimensional Classification         │
│  Pattern: Chain of Agents worker parallelism                │
│  Model: Local Ollama Qwen3-30B-A3B-Thinking (free)          │
│                                                             │
│  For each paragraph, score 0-10 on:                         │
│    - actionable_lesson   (explicit advice, directive)       │
│    - framework           (model, checklist, process)        │
│    - evidence            (case study, data, research)       │
│    - narrative_filler    (anecdote for flavor)              │
│    - justification       (persuasion, repetition)           │
│    - author_voice_core   (quotable, essential phrasing)     │
│                                                             │
│  Why local: ~2000 paragraphs × 1 call = 2000 LLM calls.     │
│  Claude would cost $5-10; Qwen3 is free and fast enough.    │
│                                                             │
│  Output: scored_paragraphs table                            │
│  Cost: $0 (local GPU, ~15-25 min per book)                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  STAGE 3: Extractive Selection (deterministic filter)       │
│  Keep paragraph if:                                         │
│    max(actionable, framework, author_voice_core) >= 7       │
│    OR (evidence >= 7 AND linked to kept actionable)         │
│                                                             │
│  Drop:                                                      │
│    narrative_filler >= 7 AND actionable < 5                 │
│    justification >= 7 (always)                              │
│                                                             │
│  Result: 30-50% of book retained VERBATIM (author's words)  │
│  Cost: $0                                                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  STAGE 4: Incremental Chapter Compression                   │
│  Pattern: BOOOOKSCORE incremental + NexusSum hierarchy      │
│  Model: Claude Sonnet 4.6 200K high-effort                  │
│                                                             │
│  For each chapter (sequential, maintains running context):  │
│    Input:                                                   │
│      - extracted verbatim paragraphs                        │
│      - running-context summary from previous chapters       │
│    Task:                                                    │
│      - Connect verbatim lessons with minimal transitions    │
│      - Deduplicate cross-paragraph repetition               │
│      - Preserve cross-references                            │
│      - Keep verbatim blocks marked (author's voice intact)  │
│    Output: chapter.md                                       │
│                                                             │
│  Context window: Sonnet 4.6 200K sufficient per chapter     │
│  Cost: ~8-15 calls per book, ~$0.40 total                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  STAGE 5: Multi-View Organizer                              │
│  Model: Claude Sonnet 4.6                                   │
│                                                             │
│  Generates parallel outputs from compressed corpus:         │
│    summary.md         2-page overview, core thesis          │
│    key-actions.md     every actionable directive, bulleted  │
│    frameworks.md      mental models + checklists            │
│    evidence.md        case studies grouped by claim         │
│    quotes.md          verbatim quotable passages            │
│    chapter01-NN.md    compressed chapters (from Stage 4)    │
│                                                             │
│  Cost: ~1 pass, ~$0.15                                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  STAGE 6: Quality Evaluation (BOOOOKSCORE-inspired)         │
│                                                             │
│  Automatic checks:                                          │
│    - Causal omission detection                              │
│    - Salience validation (compressed vs full book)          │
│    - Reference integrity (cross-chapter links resolve)      │
│    - Verbatim-block hash verification (no silent rewrites)  │
│                                                             │
│  Flag issues for manual review                              │
│  Cost: $0-0.05                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Token Budget (300-page book, ~150K raw tokens)

| Stage | Model | Input tokens | Output tokens | Cost (API) |
|---|---|---|---|---|
| Parse | deterministic | 0 | 0 | $0 |
| Classification | Ollama Qwen3-30B | ~150K | ~30K | **$0** (local) |
| Extractive filter | deterministic | 0 | 0 | $0 |
| Chapter compression | Sonnet 4.6 | ~120K | ~45K | ~$0.40 |
| Multi-view organizer | Sonnet 4.6 | ~60K | ~20K | ~$0.15 |
| Quality eval | Sonnet 4.6 | ~30K | ~5K | ~$0.05 |
| **Total** | | ~360K | ~100K | **~$0.60/book** |

On Claude Max 5x: free within plan quota. Throughput: **5-10 books/week**.

---

## Output Structure (LEARNING_PROMPT-aligned)

```
~/Workbench/STUDIES/books/<book-slug>/
├── summary.md            2-page overview + thesis + key takeaways
├── key-actions.md        Every actionable directive, bulleted, chapter-indexed
├── frameworks.md         Mental models, checklists, processes
├── evidence.md           Case studies grouped by claim
├── quotes.md             Verbatim quotable passages
├── chapter01.md          Compressed chapter (verbatim lessons + minimal glue)
├── chapter02.md
├── ...
├── chapterNN.md
├── metadata.json         {title, author, isbn, original_pages, compressed_pages,
│                          compression_ratio, classification_thresholds, model_versions}
└── .classification/      Raw classifier scores (for re-running with different filters)
    └── scored_paragraphs.parquet
```

### Example `key-actions.md` structure
```markdown
# Key Actions

## Chapter 2: Making It Obvious
- [Action] Make environmental cues for desired habits impossible to miss
  - Context: "Environment is the invisible hand shaping human behavior" (p. 82)
  - Applicability: my desk setup for daily study habit

## Chapter 3: Make It Attractive
- [Action] Bundle a habit you need with a habit you want
  - Context: "The temptation bundling strategy..." (p. 107)
  - Applicability: pair Claude Code study with morning coffee
```

---

## Comparison Matrix

| Property | Blinkist | Shortform | NotebookLM | Pure Claude 1M | **AEHD** |
|---|---|---|---|---|---|
| Preserves author voice | ❌ | ❌ | N/A | Depends | ✅ extractive-first |
| Action-item extraction | Light | Yes + exercises | ❌ | Depends | ✅ explicit view |
| Personalized filter | ❌ | ❌ | ❌ | Per-prompt | ✅ classifier tunable |
| Re-run with new filter | ❌ | ❌ | New notebook | Full rerun | ✅ reuse Stage 2 output |
| Cost per book | $8/mo sub | $25/mo sub | Free (limited) | ~$3-5 | **~$0.60** |
| Output format | Mobile blink | Web article | Notebook UI | Opaque | **LEARNING_PROMPT structure** |
| Books from any source | ❌ | ❌ | ✅ | ✅ | ✅ |
| Offline capable | ❌ | ❌ | ❌ | ❌ | ✅ (Ollama classification) |

---

## Why Each Architecture Decision Wins

| Decision | Alternative | Why this wins |
|---|---|---|
| Extractive-first | Pure abstractive | Preserves exact phrasing on lessons (*"Fragility is the inverse of antifragility"* stays as Taleb wrote it) |
| Local Qwen3 classification | Claude classification | 2000 calls/book; Claude = $5-10, Qwen3 = $0 |
| Multi-dim scoring | Binary keep/drop | Re-run with different thresholds without re-classifying |
| Incremental chapter compression | Hierarchical merge | BOOOOKSCORE: incremental preserves more detail (lesson fidelity) |
| Sonnet 4.6 200K | Opus 4.6 1M | 200K sufficient per-chapter; Sonnet 5× cheaper; 1M removed from Max 5x |
| CoA worker pattern | Single-threaded | Qwen3 classification parallelizes across paragraphs (n² → nk) |
| NexusSum hierarchy | Flat compression | Handles cross-chapter context (framework appears in ch.3, referenced in ch.9) |
| Multi-view outputs | Single summary file | `key-actions.md` standalone productivity artifact; re-usable independently |
| BOOOOKSCORE-style eval | Trust the output | Catches silent hallucinations before they enter your knowledge base |

---

## Integration with COELHONexus

Reuses existing infrastructure (~0 new modules):
- **Postgres** — `doc_corpus`, `scored_paragraphs`, `compressed_books` tables
- **MinIO** — raw books + compressed outputs
- **Ollama Qwen3-30B** — classification workhorse (already in `opencode.json`)
- **FastAPI** — new router `/digest/*` endpoints
- **Celery** — async ingestion jobs (20-40 min per book)
- **LangGraph** — orchestrates the 6-stage pipeline
- **Optional Qdrant** — cross-book semantic search (*"find all lessons on delegation across my library"*)
- **Optional Neo4j** — lesson relationship graph (*"which books contradict on X?"*)

New code (~800 lines):
```
apps/fastapi/services/book_distillation/
├── parser.py              EPUB/PDF/MOBI → structured corpus
├── classifier.py          Qwen3 multi-dim paragraph scoring
├── selector.py            Threshold-based extractive filter
├── compressor.py          Claude chapter-level incremental compression
├── organizer.py           Multi-view output generator
└── evaluator.py           BOOOOKSCORE-inspired QA

apps/fastapi/routers/digest.py   FastAPI endpoints
apps/fastapi/tasks/ingest_book.py Celery task
```

---

## Implementation Roadmap

### MVP (2 days) — validate end-to-end
1. Pick one test book (suggestion: *Atomic Habits* or *Principles* — explicit lesson structure)
2. Build `parser.py` (pypdf + regex chapter detection)
3. Build `classifier.py` with Qwen3 via Ollama local
4. Manual tuning: run classification on ch.1, inspect scores, adjust prompt
5. Build `selector.py` (deterministic filter)
6. Build `compressor.py` with Sonnet 4.6 (one chapter as proof)
7. Manual review: compare compressed ch.1 vs original
8. If quality acceptable → scale to full book

**Success criteria:**
- Compressed book is 30-50% length of original
- Verbatim lesson blocks preserved (grep for key phrases)
- `key-actions.md` reads as standalone productivity artifact
- Total cost < $1 API on Sonnet
- Total time < 45 min per book

### Phase 2 — Productionize
- Celery async ingestion
- FastAPI endpoints
- Progress tracking + resume-on-failure
- Multi-book batch processing
- OpenTelemetry observability
- Classifier prompt versioning + A/B testing

### Phase 3 — Library features
- Qdrant cross-book semantic search
- Neo4j lesson relationship graph
- Re-compression with new filter presets
- Personal recommendation engine (*"based on my compressed library, these 3 books have complementary ideas"*)

---

## Honest Caveats

1. **Classifier calibration matters.** Budget 1-2 hours tuning classification prompts + thresholds on 2-3 test books before trusting at scale.

2. **Some books don't compress well.** Memoirs, narrative non-fiction (e.g., *Sapiens*), anything where story IS the content. AEHD works best on advice-dense books (*Atomic Habits*, *Principles*, *7 Habits*, technical how-to).

3. **Copyright**: personal use only over books I own. Do not publish compressed outputs or share widely.

4. **BOOOOKSCORE eval is approximate.** Automatic quality checks catch obvious failures but not subtle lesson distortion. Periodic manual spot-check required.

5. **Qwen3-30B quality ceiling.** Good enough for classification but not for abstractive rewriting. Don't shortcut Stage 4 with local-only.

6. **First-book cost higher**: prompt tuning, parser edge cases. Subsequent books are ~$0.60 each.

7. **"Best architecture" is a 2026-snapshot claim.** Field moves fast — expect to revisit in 6-12 months. Architecture is modular: any stage can be swapped.

---

## What Independent Verification Would Strengthen

- End-to-end token measurement on first real book
- Verbatim preservation check (hash verification of kept blocks)
- Coverage spot-check: does the compressed book actually contain all major lessons?
- A/B comparison: AEHD output vs Shortform guide on same book (same title)
- Long-term utility: which compressed books do I actually re-read or reference?

---

## Sources

### Academic (primary research)
- [BOOOOKSCORE — Kim et al. (ICLR 2024)](https://arxiv.org/abs/2310.00785) — book-length summarization, 8 coherence error types, Claude 2 @ 88K best
- [Chain of Agents — Google (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/file/ee71a4b14ec26710b39ee6be113d7750-Paper-Conference.pdf) — multi-agent long-context, n² → nk complexity
- [NexusSum — Kim & Kim (ACL 2025)](https://arxiv.org/abs/2505.24575) — hierarchical multi-LLM narrative summarization
- [Learning to Summarize from Human Feedback — Stiennon et al., OpenAI 2020](https://arxiv.org/abs/2009.01325) — foundational recursive summarization
- [Abstractive Text Summarization State of the Art 2024](https://arxiv.org/html/2409.02413v1)

### Industry / products
- [Shortform vs Blinkist 2026](https://www.shortform.com/blog/hub/product/shortform-vs-blinkist/)
- [BOOOOKSCORE GitHub package](https://github.com/lilakk/BooookScore) — evaluation tooling

### Related — COELHONexus
- [LEARNING-PROMPT-RAG-ARCHITECTURE.md](../COELHONexus/docs/LEARNING-PROMPT-RAG-ARCHITECTURE.md) — sibling architecture for docs ingestion
- [ADAPTIVE-RAG-ARCHITECTURE.md](../COELHONexus/docs/ADAPTIVE-RAG-ARCHITECTURE.md) — base RAG substrate
- [NVIDIA-NIM-EMBEDDING-MODELS.md](../COELHONexus/docs/NVIDIA-NIM-EMBEDDING-MODELS.md) — embedding model reference

### Target output format
- [LEARNING_PROMPT.md](./LEARNING_PROMPT.md) — output conventions this pipeline should match
