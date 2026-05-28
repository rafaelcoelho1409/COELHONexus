# LLM-First Planner Architecture (2026-05-27)

Architectural shift in the COELHO Nexus Planner: replace UMAP+HDBSCAN+
c-TF-IDF+LLM-label+reduce (5 nodes that fail catastrophically on small
corpora) with a 4-node LLM-driven hierarchy that works for any N.

**Empirical trigger:** Browser Use (38 docs) and Claude Code (130 docs)
planner runs produced only 2 chapters each, with Claude Code's first
chapter absorbing 123/130 docs (91% in one cluster — HDBSCAN
catastrophically under-clusters small/medium corpora because UMAP's
manifold collapses when `n_neighbors` approaches N).

**Cross-references:**
- [`PLANNER-ARCHITECTURE-2026-05-17.md`](./PLANNER-ARCHITECTURE-2026-05-17.md) — committed 10-node design being replaced
- [`KD-PLANNER-SOTA-IMPROVEMENTS-2026-05-23.md`](./KD-PLANNER-SOTA-IMPROVEMENTS-2026-05-23.md) — May 23 incremental improvements (cross-encoder off_topic, embedder swap) that are obsolete after this shift
- [`DD-4FRONT-ROADMAP-2026-05-25.md`](./DD-4FRONT-ROADMAP-2026-05-25.md) — Bundle 5 retune that helped but didn't fix the fundamental issue

## 1. The problem

| Corpus | N docs | HDBSCAN output | After c-TF-IDF rescue | Result |
|---|---|---|---|---|
| Browser Use | 38 | 20 noise + 9 + 9 | 25 + 13 | 2 chapters |
| Claude Code | 130 | 6 noise + 6 + **118 (91%)** | 7 + **123 (95%)** | 2 chapters (1 silently dropped) |
| FastMCP | 252 | (older data — working) | — | 2-3 chapters |
| LangChain | 777 | 15 balanced clusters | unchanged | 5 chapters ✓ |

Root cause: UMAP `n_neighbors` (capped at 30) on small N produces a
collapsed manifold. HDBSCAN then finds 1-2 stable modes regardless of
underlying topic diversity. c-TF-IDF rescue absorbs noise into existing
buckets — it doesn't recover missing structure. The Label LLM produces
meaningless mega-labels ("Claude Code Setup" for 123 docs spanning
install + hooks + mcp + plugins + sdk + slash commands + agents).

## 2. The SOTA convergence (May 2026)

| Source | Finding | Relevance |
|---|---|---|
| arXiv 2510.03174 (Oct 2025) | Long-context LLM zero-shot beats neural topic models on small/medium corpora | Foundational — drives our shift |
| TopicGPT (NAACL 2024, refined 2025) | LLM Propose→Refine→Assign hierarchy outperforms LDA/BERTopic on human topic quality | Pattern we adopt |
| GoalEx (arXiv 2305.13749) | ILP-selected cluster set with coverage guarantee | Algorithm for chapter_select |
| AutoSurvey2 (arXiv 2510.26012) | Retrieval-guided hierarchical outline FIRST, then per-section retrieve+write | Outline-first principle |
| SurveyGen-I (arXiv 2508.14317) | Adaptive outline that grows when evidence saturates a section | Future enhancement |
| LITA (arXiv 2412.12459) | Hybrid: embeddings cluster, LLM reassigns ambiguous docs to existing OR NEW topics | Pattern for >2000 doc fallback |
| Diátaxis + clig.dev | Documentation IA — Tutorial/How-To/Reference/Explanation + CLI command-tree | Structural seeding pattern |

## 3. The new architecture

```
corpus_load (KEEP)
   ↓
embed_corpus (KEEP — feeds off_topic for noise filtering)
   ↓
off_topic (KEEP — filters before LLM ingests)
   ↓
[FORK on KD_PLANNER_LLM_FIRST]
   ├── true (default) — LLM-FIRST PATH (NEW)
   │   doc_distill         (adaptive: skip for N≤80, parallel summaries for N>80)
   │     ↓
   │   chapter_propose     (LLM ingests distillates+seeds, proposes 6-15 chapters)
   │     ↓
   │   chapter_assign      (per-doc LLM scores against each proposal)
   │     ↓
   │   chapter_select      (greedy coverage; structural-seed pinning; <3-doc pruning)
   │     ↓
   └── false — LEGACY PATH (existing 5 nodes, kept as fallback)
      cluster → refine → label → reduce
   ↓
order_chapters (KEEP)
   ↓
plan_write (KEEP)
```

### Why we keep `embed_corpus` and `off_topic` in the LLM-first path

- `off_topic` filters genuinely unrelated docs (e.g. license files, contributing guides) BEFORE the LLM ingests. Saves tokens and removes noise from propose context.
- `embed_corpus` is needed for off_topic's cross-encoder gate.

### Why we drop `cluster` / `refine` / `label` / `reduce` in the LLM-first path

- `cluster` (UMAP+HDBSCAN+c-TF-IDF): proven to under-cluster small N.
- `refine`: pointless if no clusters were produced.
- `label`: folded into `chapter_propose` (LLM names chapters at propose time).
- `reduce`: replaced by `chapter_select` (greedy coverage instead of LLM merge of bad clusters).

## 4. Per-node specifications

### `doc_distill/`

**Purpose:** produce a compact semantic representation of each doc that fits the proposer's context budget.

**Input:** `relevant_files` (list of MinIO source keys, post-off_topic)

**Adaptive behavior:**
- N ≤ 80: pass-through (no LLM call; downstream uses doc bodies directly via raw load).
- 80 < N ≤ 2000: parallel LLM call per doc — 1-sentence summary + 5 key terms.
- N > 2000: same as 80<N≤2000 but with sharded propose (hierarchical, deferred).

**Per-doc LLM output:**
```json
{
  "summary":   "1 sentence (12-40 words) describing what this file teaches.",
  "key_terms": ["term1", "term2", "term3", "term4", "term5"]
}
```

**Concurrency:** 16-32 via existing FGTS-VA rotator (`chat_judge_bandit_async`).

**Wall time estimate:** 5-10 sec for N=38, ~2-5 min for N=777.

**State:** `doc_distill_ref` (MinIO key of {key→DocDistillate} JSON) + `doc_distill_stats`.

### `chapter_propose/`

**Purpose:** generate 6-15 candidate chapter proposals that cover the corpus surface area.

**Input:** `doc_distill_ref` (or raw bodies if skip-pass) + `relevant_files`.

**Structural-seed pre-step:**
- Regex extract markdown H1/H2 headings (`^# `, `^## `).
- File-tree namespace prefixes (e.g. `commands/plugin/...` → namespace `plugin`).
- For CLI-detected corpora (heuristic: heading patterns like `claude <cmd>`), force one seed per top-level command.

**LLM call (single):**
- Concatenates: structural seeds + all distillates (or full bodies for small N).
- Prompt: "Propose 6-15 chapters covering the full surface area of {framework}. Each chapter has a title (3-7 words), description (1 sentence), 5-15 key concepts. Aim for balance."
- Response format: `json_schema` with Pydantic `ChapterProposalList`.
- Provider: defaults to long-context (Gemini 2.5 1M / Cerebras Llama-3.3-70B 128K via rotator).

**Output:**
```json
{
  "proposals": [
    {"title": "Authentication", "description": "...", "key_concepts": [...]},
    ...
  ]
}
```

**State:** `chapter_proposals_ref` + `propose_stats`.

### `chapter_assign/`

**Purpose:** score each doc's membership against each proposed chapter.

**Input:** `doc_distill_ref` + `chapter_proposals_ref`.

**Per-doc LLM call (parallel, concurrency 16-32):**
- Prompt: shows the doc's summary + key terms + ALL proposals with descriptions.
- Returns confidence 0.0-1.0 per chapter. Multi-assignment allowed.
- Response format: `json_schema` (`DocAssignment`).

**Output:** sparse matrix `doc_id → list[(chapter_id, confidence)]`.

**State:** `chapter_doc_assignments_ref` + `assign_stats`.

### `chapter_select/`

**Purpose:** pick the minimum chapter set covering ≥95% of docs above confidence threshold. Hard-pin structurally-seeded chapters.

**Input:** `chapter_proposals_ref` + `chapter_doc_assignments_ref`.

**Algorithm (greedy coverage, no LLM):**
1. Build doc × chapter matrix from assignments.
2. Pin structurally-seeded proposals (from chapter_propose's seed metadata).
3. Greedy: while uncovered docs exist AND chapters remain to consider:
   - Pick chapter with highest sum of confidences from uncovered docs.
   - Mark docs above `τ_confidence = 0.5` as covered.
4. Drop chapters with `<3` assigned docs UNLESS pinned.
5. Output schema matches existing `reduce_node` output so downstream `order_chapters` + `plan_write` need no changes.

**Output:**
```json
{
  "chapters": [
    {"title": "...", "description": "...", "member_doc_keys": [...], "order": 1},
    ...
  ]
}
```

**State:** writes to `chapter_plan_ref` (SAME field as legacy reduce — clean downstream integration) + `select_stats`.

## 5. Feature flag + fallback

- `KD_PLANNER_LLM_FIRST=true` (default) — uses new path
- `KD_PLANNER_LLM_FIRST=false` — uses legacy path (cluster→refine→label→reduce)

Legacy path stays compiled but unwired by default. Kept for emergency rollback. Removed in a future cleanup pass once 4+ corpora validate the new path.

## 6. Expected outputs per corpus

| Corpus | N | Legacy output | LLM-first projected |
|---|---|---|---|
| Browser Use | 38 | 2 chapters (13+25) | **5-8 chapters** balanced |
| Claude Code | 130 | 2 chapters (123+7) | **8-12 chapters** (claude install/auth/plugins/hooks/mcp/slash/agents/sdk/settings/…) |
| FastMCP | 252 | 2-3 chapters | 6-10 chapters |
| LangChain | 777 | 5 chapters (validated May 23) | 8-14 chapters |

## 7. Ship plan

Single landing — Phase 0 (this doc) + Phase 1 (4 nodes) + Phase 2 (graph wiring) ship in one wave. Phase 3 (validation) happens via user re-runs. Phase 4 (legacy node removal) deferred until after 4-corpus validation passes.

## 8. References

- arXiv 2510.03174 — Topic Modeling as Long-Form Generation (Oct 2025)
- arXiv 2311.01449 — TopicGPT
- arXiv 2305.13749 — GoalEx
- arXiv 2510.26012 — AutoSurvey2 (Oct 2025)
- arXiv 2508.14317 — SurveyGen-I
- arXiv 2412.12459 — LITA
- Diátaxis docs IA framework (diataxis.fr)
- CLI Guidelines (clig.dev)
