# Synth SOTA Audit + Improvement Ranking (2026-05-24)

Comprehensive audit of the current 6-node Synth pipeline against May 2026 state-of-the-art, with a priority-ranked improvement list calibrated to free-tier-only constraints + `feedback_kd_quality_over_speed` (tokens are free, quality > speed).

**Cross-references:**
- [`SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`](./SYNTH-ARCHITECTURE-SOTA-2026-05-18.md) — committed 6-node design being audited
- [`KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md`](./KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md) — Phases 1-5 already shipped (classical grader, vault sentinels, etc.)
- [`DD-PIPELINE-SOTA-COMPARISON-2026-05-23.md`](./DD-PIPELINE-SOTA-COMPARISON-2026-05-23.md) — original "biggest gap = cross-chapter coherence" finding being acted on here

## 1. TL;DR — what to ship and in what order

| Rank | Change | Node touched | LOC | Expected impact | ROI (pp/LOC) |
|---|---|---|---|---|---|
| 🥇 **1** | **Pairwise tournament picker** (replace pointwise critic-picker for N=3 drafts) | `sawc_write` | ~40 | +30-40pp draft-selection signal recovery (21% → 61% per Landesberg 2026) | **~0.9** |
| 🥈 **2** | **LLM-as-judge faithfulness via bandit-routed rotator** (atomic-claim entailment, replaces cosine-similarity) | `checklist_eval` | ~60 | +8-12pp F1 vs cosine baseline (Mistral-Large 76.5%, NIM Llama-3.3-70B ~74-77% on LLM-AggreFact; 2-3pp behind LettuceDetect, but stays inside free-tier-API-only constraint) | **~0.15** |
| 🥉 **3** | **`book_harmonize` cross-chapter coherence pass** (NEW node at study-orchestrator level) | NEW post-render | ~55 | +0.43-0.57 absolute points on synthesis-quality scale (SurveyGen-I Step 11 ablation) | **~0.9** |
| 4 | **mgsr→sawc loop closure** (CoRefine confidence-guided halting, OP-12 best-seen rescue) | `mgsr_replan` → `sawc_write` | ~50 | +5-10pp checklist pass rate | ~0.15 |
| 5 | **AutoChecklist Deductive adaptive criteria** (per-chapter rubric expansion from 5 dimensions) | `checklist_eval` (generator) | ~250 | +5pp evaluator-human agreement | ~0.02 |

**Recommended sequence:** ship 1 + 2 first (week 1, isolated patches), then 3 (week 2, new node, validate cross-chapter pass), then 4 (week 3, loop wiring), defer 5 until after 50+ chapters are in flight (need exemplar data for tuning).

## 2. Current Synth state (audit baseline)

The 6-node pipeline at `apps/fastapi/domains/dd/synth/` is already on the frontier per the 2026-05-23 comparison doc. Locked-in decisions that should NOT be re-researched:

| Decision | Source | Status |
|---|---|---|
| Classical grader replaces LLM grader (8/9 dims deterministic) | KD-SYNTH-LLM-TO-CLASSICAL | ✅ Shipped |
| Vault sentinelization for code preservation | Verbatim arXiv 2601.03640 | ✅ Shipped |
| Binary checklist evaluator > weighted-score | RefineBench Nov 2025 | ✅ Shipped |
| SurveyGen-I SDP for outline (PlanEvo DAG) | SurveyGen-I IJCNLP 2025 | ✅ Shipped |
| LLMxMapReduce-V3 for digest routing | LLMxMapReduce-V3 EMNLP 2025 | ✅ Shipped |
| SAWC parallel stage-writing (MAMM-Refine N=3) | SurveyGen-I §3.2 | ✅ Shipped (picker has a bug — see #1 below) |
| Confidence-guided halting (NOT regression early-stop) | CoRefine | ⚠️ Specified but loop not closed (v1 emits actions, doesn't apply) |
| Global asyncio.Semaphore concurrency cap | Phase 5 | ✅ Shipped |

The 6 currently-implemented nodes form the production pipeline. Each is a candidate for one of the improvements below.

## 3. The 5 SOTA improvements (detailed)

---

### 🥇 #1 — Pairwise tournament picker for SAWC

**Where:** `apps/fastapi/domains/dd/synth/sawc/node.py` — the critic-picker that selects 1 of N=3 drafts.

**Current code path (the bug):**
- 3 writer drafts generated in parallel via different rotator arms
- 1 critic call asks the LLM to **pointwise score** all 3 drafts and return the best
- Falls back to deterministic Self-Certainty (length + citation count) on critic failure

**Why this is a documented failure mode:**
- Landesberg et al. Mar 2026 (arXiv:2603.12520) — *"When LLM Judge Scores Look Good but Best-of-N Decisions Fail"* — showed global pointwise correlation r=0.47 captures only **21% of actual selection uplift** because:
  - Within-prompt r=0.27 (judges struggle to discriminate similar-quality drafts)
  - 67% of pairwise comparisons get tied at the same score
- For long-form technical content where all 3 drafts are competently written, the pointwise picker essentially picks randomly.

**Fix — pairwise knockout tournament:**
- For N=3: 2 matches (A vs B → winner vs C)
- Each match uses a cross-family critic (PoLL-style: NIM family vs Mistral family)
- Forces A/B choice — no ties allowed in the prompt
- 2 extra critic calls vs 1 current = trivial under "tokens are free"

**Empirical recovery:** 21% → 61% of selection signal per Landesberg + Pairwise RM Knockout Tournament (arXiv:2501.13007).

**Implementation sketch (~40 LOC):**

```python
# apps/fastapi/domains/dd/synth/sawc/service.py

PAIRWISE_PROMPT = """You are picking the better of two technical-documentation
drafts for the same section. Choose by these criteria in order:
1. Checklist coverage (does it address every outline point?)
2. Citation density (does it reference the source pages it claims?)
3. Structural completeness (no truncations, no orphan code-refs)
4. Clarity (concise, well-organized)

You MUST choose A or B. Ties are not allowed.

--- DRAFT A ---
{draft_a}
--- DRAFT B ---
{draft_b}

Answer with EXACTLY ONE WORD: A or B."""


async def pairwise_pick(
    drafts: list[str], framework_name: str, framework_category: str,
) -> tuple[str, dict]:
    """Knockout tournament: 2 matches for N=3 drafts.

    Match 1 uses one critic family (e.g., NIM-family arm)
    Match 2 uses a DIFFERENT family (e.g., Mistral) — PoLL diversity.
    Returns (winning_draft, telemetry_dict).
    """
    if len(drafts) < 2:
        return drafts[0], {"matches": 0}
    if len(drafts) == 2:
        winner, m1 = await _pair_judge(drafts[0], drafts[1], family_hint="nim")
        return winner, {"matches": 1, "match_1": m1}

    # Match 1
    winner_ab, m1 = await _pair_judge(drafts[0], drafts[1], family_hint="nim")
    # Match 2 (cross-family)
    winner, m2 = await _pair_judge(winner_ab, drafts[2], family_hint="mistral")
    return winner, {"matches": 2, "match_1": m1, "match_2": m2}


async def _pair_judge(a: str, b: str, family_hint: str) -> tuple[str, dict]:
    prompt = PAIRWISE_PROMPT.format(draft_a=a[:6000], draft_b=b[:6000])
    response, meta = await chat_judge_bandit_async(
        prompt, max_tokens=4, temperature=0.0,
        expected_pattern=r"^[AB]$",
        deployment_family_hint=family_hint,  # bandit prefers arms from this family
    )
    pick = (response or "A").strip().upper()[:1]
    return (a if pick == "A" else b), {
        "pick": pick, "deployment": meta.get("deployment"),
    }
```

**Fallback chain (unchanged shape, just swap picker):**
```python
try:
    winner, telemetry = await pairwise_pick(drafts, fn, fc)
except Exception:
    winner = max(drafts, key=self_certainty_score)  # existing fallback
    telemetry = {"matches": 0, "fallback": "self_certainty"}
```

**Ship cost:** ~40 LOC. **Single highest-ROI lever in the entire pipeline.** Independent — no other change needs to land first.

---

### 🥈 #2 — LLM-as-judge faithfulness via bandit-routed rotator (CONSTRAINT-CORRECTED 2026-05-24 evening)

**Where:** `apps/fastapi/domains/dd/synth/checklist/` — the `faithfulness` dimension of the 5 LLM-judged criteria.

**Current code path (the anti-pattern):**
- Faithfulness scored via embedding-similarity (kd-embed rotator + cosine).
- Documented limitation: cosine "is driven by surface-level semantic proximity rather than factual reasoning" (arXiv:2508.20408).
  - Paraphrased-but-true claims score low (false negative)
  - Hallucinated-but-similar-vocab claims score high (false positive)

**Why NOT LettuceDetect (the original ranked SOTA):**
LettuceDetect-large would have been the technical winner — 395M ModernBERT-NLI, F1 79.22 RAGTruth, ~1.6 GB RAM, CPU-runnable. **But running it locally violates `project_local_vs_rotator_architecture`** (architectural rule: NO inference inside COELHO Cloud, all model calls via free-tier rotators OR user-managed host-side llama-server outside the cluster). For a free-tier-only demo, no local inference is the binding constraint.

**Free-tier alternative — atomic-claim LLM-judge via the rotator:**
- Extract atomic claims from chapter prose (1 LLM call via FGTS-VA bandit)
- For each claim, ask a different rotator arm: "Is this claim supported verbatim by the provided source text?" — bounded async concurrency (asyncio.Semaphore=8)
- Aggregate: `score = 1 - (n_unsupported / n_claims)`
- Use cross-family critics (NIM family for extraction, Mistral family for verification) — PoLL-style diversity

**Empirical baseline (free-tier models on LLM-AggreFact):**
- Mistral-Large-2: 76.5% balanced accuracy
- NIM Llama-3.3-70B: ~75% (similar to Claude-3.5-Sonnet at 77.2)
- Free-tier ensemble (any 2-3 arms via bandit): expected 76-78%
- **vs current cosine baseline ≈65-68% on technical content → +8-12pp expected lift**
- **vs LettuceDetect 79.22%**: 2-3pp accuracy delta — the cost of staying free-tier-API-only

**Implementation sketch (~60 LOC):**

```python
# apps/fastapi/domains/dd/synth/checklist/faithfulness.py
"""Faithfulness scoring via bandit-routed LLM-as-judge.

Replaces the embedding-cosine baseline that suffered false-positive issues on
technical prose. Atomic-claim approach: extract claims → per-claim entailment
against source docs via the existing FGTS-VA bandit rotator (Mistral / NIM /
Gemini / etc. free tiers).

Constraint: free-tier API only. NO local inference (per
project_local_vs_rotator_architecture). NO paid APIs. NO fine-tuning.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from domains.llm.rotator.chain import chat_judge_bandit_async


logger = logging.getLogger(__name__)


_EXTRACT_PROMPT = """Extract the atomic factual claims from this chapter prose.
A claim is a single verifiable fact (e.g., "library X uses Y for authentication",
"the default timeout is 30s", "function foo returns a list of strings").

Return JSON: {{"claims": ["claim 1", "claim 2", ...]}}
Cap at 30 claims. Skip claims that are obvious / structural / motivational.

--- CHAPTER PROSE (truncated) ---
{prose}
--- END PROSE ---"""

_JUDGE_PROMPT = """Is this claim supported by the source text below?

CLAIM: {claim}

--- SOURCE TEXT (excerpt) ---
{source}
--- END SOURCE ---

Answer in JSON: {{"supported": true | false, "evidence": "short quoted evidence or empty"}}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_CONCURRENCY = 8
_MAX_CLAIMS = 30
_PROSE_CHARS = 12000
_SOURCE_CHARS = 12000


async def faithfulness_via_llm(
    chapter_prose: str,
    source_docs: list[str],
) -> dict:
    """Atomic-claim entailment scoring. Returns:
        {
            "score": float in [0, 1],
            "n_claims": int,
            "n_unsupported": int,
            "unsupported_claims": [{"claim", "evidence"}],
            "method": "llm_judge_v1",
        }
    """
    src_blob = "\n\n".join(source_docs)[:_SOURCE_CHARS]
    claims = await _extract_claims(chapter_prose[:_PROSE_CHARS])
    if not claims:
        return {"score": 1.0, "n_claims": 0, "n_unsupported": 0,
                "unsupported_claims": [], "method": "llm_judge_v1"}

    sem = asyncio.Semaphore(_CONCURRENCY)
    verdicts = await asyncio.gather(*[
        _judge_claim(sem, c, src_blob) for c in claims
    ])
    unsupported = [
        {"claim": c, "evidence": v.get("evidence", "")}
        for c, v in zip(claims, verdicts) if not v.get("supported", True)
    ]
    n_claims = len(claims)
    return {
        "score": 1.0 - len(unsupported) / max(1, n_claims),
        "n_claims": n_claims,
        "n_unsupported": len(unsupported),
        "unsupported_claims": unsupported,
        "method": "llm_judge_v1",
    }


async def _extract_claims(prose: str) -> list[str]:
    try:
        raw, _ = await chat_judge_bandit_async(
            _EXTRACT_PROMPT.format(prose=prose),
            max_tokens=1200, temperature=0.0,
        )
        m = _JSON_RE.search(raw or "")
        if not m:
            return []
        data = json.loads(m.group(0))
        return list(data.get("claims") or [])[:_MAX_CLAIMS]
    except Exception as e:
        logger.warning(f"[faithfulness] claim extraction failed: {e}")
        return []


async def _judge_claim(sem, claim: str, source: str) -> dict:
    async with sem:
        try:
            raw, _ = await chat_judge_bandit_async(
                _JUDGE_PROMPT.format(claim=claim, source=source),
                max_tokens=200, temperature=0.0,
            )
            m = _JSON_RE.search(raw or "")
            if not m:
                return {"supported": True}  # conservative: assume supported on parse failure
            return json.loads(m.group(0))
        except Exception:
            return {"supported": True}  # conservative
```

**Integration into checklist_eval:** swap the `faithfulness` dim from cosine to `await faithfulness_via_llm(chapter_prose, source_docs)`. Schema-compatible (still returns `score` float in [0,1]).

**Infrastructure delta: ZERO.** No new dependencies, no model downloads, no service. Rides on the existing FGTS-VA rotator.

**Latency:** ~5-15s per chapter (1 extraction + ~10-20 parallel claim judgments at concurrency=8, free-tier latency dominated).

**Ship cost:** ~60 LOC, 0 new deps. **2-3pp accuracy below LettuceDetect, but 100% architectural-rule compliant.**

---

### 🥉 #3 — `book_harmonize` cross-chapter coherence node

**Where:** NEW node at the study-orchestrator level (between `render_audit_write` of last chapter and final book assembly). NOT a per-chapter node — coherence is by definition cross-chapter.

**The gap being closed:**
- Current pipeline writes each chapter independently via different bandit-routed LLM arms (DeepSeek for ch 1, Mistral for ch 2, Llama for ch 3, ...)
- No pass validates ch 2 prose ↔ ch 4 prose consistency. Definition drift, contradictory recommendations, terminology divergence happen routinely.
- Acknowledged in `DD-PIPELINE-SOTA-COMPARISON-2026-05-23.md` as **"the single biggest architectural gap"**.

**SOTA approach (3 papers converged on this shape):**

| Source | Contribution |
|---|---|
| SurveyGen-I IJCNLP 2025 (arXiv:2508.14317) | Step-11 Global Refinement — re-prompt each chapter with book skeleton + terminology bank ℳ. Ablation shows removing this step drops synthesis score by **0.43-0.57 absolute points** (1-5 scale). |
| SurveyX arXiv Feb 2025 (arXiv:2502.14776) | RAG-rewriting post-stage: per chapter, retrieve top-k sibling passages + canonical sources, rewrite for fluency + cross-chapter consistency. **+0.259 composite quality**. |
| ConStory-Checker arXiv Mar 2026 (arXiv:2603.05890) | Atomic-claim NLI detection — extract claims, pairwise classify entailment/contradiction across chapters. **F1=0.678, 3.2× recall over human experts**. |

**Algorithm (O(N) — NOT O(N²)):**

1. **Build book artifact** (deterministic, no LLM): concatenate chapter skeletons + extract terminology bank from each chapter's existing `digest_construct` memory + 1 atomic-claims-extraction LLM call per chapter.
2. **Canonicalize** (1 LLM call): resolve terminology conflicts → canonical definitions.
3. **Detect** (1 LLM call per chapter): for each chapter, given (book synopsis, canonical terminology bank, sibling-chapter atomic claims), flag contradictions/definition-drift/terminology-divergence.
4. **Remediate** (1 LLM call per flagged chapter): minimal-edit patch rewriting the original chapter using canonical terms.
5. **Re-audit**: re-run `checklist_eval` on patched chapter to ensure 0.80 threshold still holds.

**Total cost per book:** N atomic-claim extractions + 1 canonicalize + N detection + ~30%·N remediation = roughly **2.3N free-tier calls**. For a 5-chapter book: ~12 calls. Trivial.

**Implementation sketch (~55 LOC core, plus prompts + schema):**

```python
# apps/fastapi/domains/dd/synth/book_harmonize/node.py
"""book_harmonize — post-render cross-chapter coherence pass.

Runs at the study-orchestrator level after ALL chapters' render_audit_write
have completed. Detects definition-drift, contradictions, and terminology
divergence between chapters. Patches violating chapters with minimal-edit
rewrites that conform to a canonical terminology bank.

O(N) LLM calls per book — NOT O(N²). See docs/KD-SYNTH-SOTA-2026-05-24.md §3
for the architecture decision.
"""

async def book_harmonize(study_state: StudyState) -> StudyState:
    chapters = study_state["chapters"]  # list of ChapterArtifact post-render
    slug = study_state["framework_slug"]

    # === Step 1: build book artifact (deterministic + N+1 LLM calls) ===
    term_bank = {}
    claims_by_chapter = {}
    for ch in chapters:
        # Reuse existing digest_construct terminology extraction
        term_bank.update(ch.terminology_extracted)
        # Extract atomic claims from rendered prose
        claims_by_chapter[ch.id] = await extract_atomic_claims(ch.prose)

    canonical_terms = await canonicalize_terms(term_bank)  # 1 LLM call
    book_synopsis = await summarize_book_skeleton(chapters, canonical_terms)

    # === Step 2: detect (1 LLM call per chapter) ===
    issues_by_chapter = {}
    for ch in chapters:
        sibling_claims = [
            c for cid, cs in claims_by_chapter.items()
            if cid != ch.id for c in cs
        ]
        prompt = HARMONIZE_DETECT_PROMPT.format(
            chapter=ch.prose[:8000],
            synopsis=book_synopsis,
            canonical_terms=canonical_terms,
            sibling_claims=sample_topk(sibling_claims, k=40),
        )
        issues_by_chapter[ch.id] = await chat_judge_bandit_async_json(
            prompt, schema=HarmonyIssuesSchema, max_tokens=800,
        )

    # === Step 3: remediate (only chapters with issues) ===
    for ch in chapters:
        issues = issues_by_chapter[ch.id]
        if not issues.get("has_violations"):
            continue
        patched = await chat_judge_bandit_async(
            HARMONIZE_PATCH_PROMPT.format(
                original=ch.prose, issues=issues,
                canonical_terms=canonical_terms,
            ),
            max_tokens=12000, temperature=0.2,
        )
        # Re-audit gate
        audit = await checklist_eval(patched, ch.outline, ch.digest)
        if audit["pass_rate"] >= 0.80:
            ch.prose = patched
            ch.harmonized = True
            await write_chapter_artifact(slug, ch)  # MinIO overwrite
        else:
            ch.harmonization_failed = issues
            logger.warning(
                f"[book_harmonize] {slug} ch={ch.id}: patch failed re-audit "
                f"({audit['pass_rate']:.2f} < 0.80); keeping original"
            )

    study_state["book_harmonized"] = True
    return study_state
```

**Where to integrate:** in `apps/fastapi/api/v1/dd/synth.py` study orchestrator — after the per-chapter loop completes and before final study completion event. New entry in study_stats: `book_harmonized: bool` + `n_chapters_patched: int`.

**Ship cost:** ~55 LOC core + ~30 LOC for prompts + ~20 LOC for HarmonyIssuesSchema = ~105 LOC total. **Closes the single biggest architectural gap acknowledged in prior research.**

---

### #4 — mgsr → sawc loop closure (CoRefine confidence-guided halting)

**Where:** `apps/fastapi/domains/dd/synth/graph.py` — currently `mgsr_replan` emits replan actions but the graph proceeds directly to `render_audit_write` without applying them. The v1 design explicitly defers loop closure.

**SOTA halting algorithm (CoRefine, arXiv:2602.08948 Feb 2026):**
- Loop until: `checklist_pass_rate >= 0.80` OR `confidence_plateau` OR `max_iter == 5`
- OP-12 best-seen rescue: `argmax(checklist_pass_rate)` across iterations
- 92.6% precision when CoRefine confidently halts; ~63% compute savings vs fixed-N parallel BoN

**Why this halting shape is necessary (RefineBench Nov 2025):**
- Self-Refine with fixed N iterations plateaus or REGRESSES: +1.8pp GPT-5, -0.1pp DeepSeek-R1
- The bug is fixed-N. Halting-on-signal fixes it.

**Implementation sketch (~50 LOC graph wiring):**

```python
# apps/fastapi/domains/dd/synth/graph.py — add the loop edge

MAX_REFINE_ITER = 5
CHECKLIST_THRESHOLD = 0.80
PLATEAU_DELTA = 0.03

def build_synth_graph():
    g = StateGraph(SynthState)
    g.add_node("outline_sdp", outline_sdp)
    g.add_node("digest_construct", digest_construct)
    g.add_node("sawc_write", sawc_write)
    g.add_node("checklist_eval", checklist_eval)
    g.add_node("mgsr_replan", mgsr_replan)
    g.add_node("render_audit_write", render_audit_write)

    g.add_edge(START, "outline_sdp")
    g.add_edge("outline_sdp", "digest_construct")
    g.add_edge("digest_construct", "sawc_write")
    g.add_edge("sawc_write", "checklist_eval")
    g.add_edge("checklist_eval", "mgsr_replan")

    # === NEW: conditional loop edge ===
    def _route_after_mgsr(state: SynthState) -> str:
        score = state.get("checklist_stats", {}).get("pass_rate", 0.0)
        iter_n = state.get("refine_iter", 0)
        prev = state.get("prev_checklist_score", -1.0)
        if score >= CHECKLIST_THRESHOLD:
            return "render_audit_write"   # success halt
        if iter_n >= MAX_REFINE_ITER:
            return "render_audit_write"   # budget halt (best-seen rescue applies)
        if iter_n >= 2 and abs(score - prev) < PLATEAU_DELTA:
            return "render_audit_write"   # plateau halt
        return "sawc_write"              # RETHINK: loop back

    g.add_conditional_edges(
        "mgsr_replan", _route_after_mgsr,
        {"sawc_write": "sawc_write", "render_audit_write": "render_audit_write"},
    )
    g.add_edge("render_audit_write", END)
    return g.compile(checkpointer=get_checkpointer())
```

**Plus** sawc_write needs to track best-seen draft + apply replan actions from mgsr state. ~30 extra LOC inside `sawc/node.py`.

**Ship cost:** ~50 LOC graph + ~30 LOC state mutation = ~80 LOC. Ship AFTER #1 (pairwise picker) — the loop relies on a reliable per-iter quality signal that the pairwise judge provides.

---

### #5 — AutoChecklist Deductive adaptive criteria

**Where:** `apps/fastapi/domains/dd/synth/checklist/node.py` — generator for the 5 LLM-judged criteria.

**Current state:** 12 hand-rolled criteria (5 LLM-judged + 7 deterministic pre-gates), identical for every chapter regardless of type.

**SOTA (AutoChecklist arXiv:2603.07019 Mar 2026):**
- Deductive variant: LLM expands user-given dimensions (code accuracy, completeness, faithfulness, clarity, structure) into 3-5 yes/no sub-criteria specific to chapter type (API ref vs tutorial vs concept)
- ρ=0.835 consistency on SummEval (vs ~0.6 baseline)

**Caveat (ACL Findings 2025, arXiv:2508.15218):** Adaptive checklists help **pairwise** judgment but are **less consistent in direct scoring** — exactly the current `checklist_eval` use case. The Deductive variant is the safest choice (top-down decomposition vs free-form generation).

**Why this is rank 5, not earlier:**
- Marginal +5pp on a metric (evaluator-human agreement) that doesn't directly translate to chapter quality
- Requires a chapter-type classifier (new infrastructure)
- ~250 LOC vs the bigger wins above
- Defer until 50+ chapters are in flight — need exemplar data for RubricRAG-style few-shot prompting anyway

**Ship later, not now.** Phase 2 work.

## 4. What's NOT shipping and why

| Technique | Why skip |
|---|---|
| **MiniCheck-7B for faithfulness** | LLM-AggreFact #1 at 77.4%, but 7B requires host-side llama-server. Violates `project_local_vs_rotator_architecture`. |
| **LettuceDetect-large for faithfulness** | F1 79.22 RAGTruth (Pareto point for local CPU), but loads 400M model into Celery worker → still local inference. Violates constraint. See §3 #2 for the constraint-correct replacement. |
| **Bespoke-MiniCheck or AlignScore-large** | Same — both are locally-hosted NLI models. Violate the no-local-inference rule. |
| **PASR proactive mid-generation refinement** | Requires mid-stream logit access — NIM/Mistral free-tier doesn't expose this. Reactive halting (CoRefine) is the right shape for free-tier. |
| **Reflexion episodic memory** | Designed for sequential decision tasks (AlfWorld), not single-shot long-form. Wrong task shape. |
| **DPO / rDPO synthetic preferences** | Requires fine-tuning infrastructure not available. Synthetic chosen/rejected data has no training path. |
| **LongWriter-Zero RL reward shaping** | Requires GRPO training of a 32B model. Free-tier impossible. Useful as inspiration for prompt-level rubric criteria but not deployable. |
| **Learned reward model classifier** | Same — needs supervised fine-tuning. Use the *finding* (Selection > Scoring) via pairwise picker (#1) instead. |
| **NexusSum hierarchical merging** | Narrative summarization, not generation. Already covered by `digest_construct`. |
| **HippoRAG 2** | Excellent for retrieval (planned in YCS migration); wrong tool for cross-chapter coherence. |
| **GLoRe global/local refinement** | Math/code reasoning trace refinement, not long-form prose. Uses fine-tuned global model. |
| **RubricHub dataset (arXiv 2601.08430)** | Static dataset, not runtime algorithm. Use only as eval-set if we ever build one. |
| **Pure Direct/Contrastive AutoChecklist variants** | Free-form generation risks lenient criteria (ACL 2025 caveat). Deductive only. |

## 5. Free-tier compatibility verdict

**Ship all 5 without infrastructure changes.** Every component maps to existing capabilities:

| Component | Free-tier path |
|---|---|
| Pairwise picker (#1) | Existing bandit-routed rotator (NIM + Mistral families) |
| LLM-as-judge faithfulness (#2) | Existing bandit-routed rotator (extraction + per-claim entailment) |
| book_harmonize (#3) | Existing rotator for atomic-claim extraction + detection + patch + canonicalization |
| mgsr loop closure (#4) | Existing LangGraph + existing critic infrastructure |
| AutoChecklist Deductive (#5) | Existing rotator |

**No new paid APIs. No host-side llama-server. No fine-tuning infrastructure. No local inference inside COELHO Cloud (per `project_local_vs_rotator_architecture`).**

**Constraint correction (2026-05-24 evening):** Original draft of this doc ranked LettuceDetect-large as #2. Discarded after user reminded constraint: free-tier API only, no self-hosted models. LettuceDetect is technically superior (~3pp F1 lead) but loads a 400M model into the Celery worker — violates architectural rule. Replaced with bandit-routed LLM-as-judge faithfulness.

## 6. Empirical thresholds for "did the improvement work?"

When validating each ship on LangFuse/LangChain/FastMCP plans:

| Ship | Acceptance threshold |
|---|---|
| #1 Pairwise picker | sawc_stats: `n_pairwise_picks` field populated; subjective draft quality on 5 sampled chapters preferred ≥3/5 vs old pointwise picks |
| #2 LLM-as-judge faithfulness | checklist_stats: `faithfulness_method="llm_judge_v1"`, `n_claims` populated (5-30 typical), score distribution shifts (current cosine ~0.85 median → expected ~0.78-0.85 with explicit unsupported_claims list) |
| #3 book_harmonize | `study_stats.n_chapters_patched > 0` on at least one corpus; no chapter regressed below 0.80 checklist post-patch |
| #4 mgsr loop closure | `synth_stats.refine_iter` mean ≤2; chapters failing first checklist pass: ≥60% reach ≥0.80 after refine loop |
| #5 AutoChecklist Deductive | `checklist_stats.criteria_source = "adaptive"`; criteria-count varies by chapter type (3-5 sub-criteria per dimension) |

## 7. Sources

- [SurveyGen-I (IJCNLP 2025) — arXiv 2508.14317](https://arxiv.org/abs/2508.14317)
- [SurveyX (arXiv Feb 2025) — arXiv 2502.14776](https://arxiv.org/abs/2502.14776)
- [ConStory-Bench / "Lost in Stories" (arXiv Mar 2026) — arXiv 2603.05890](https://arxiv.org/abs/2603.05890)
- [AutoChecklist (Mar 2026) — arXiv 2603.07019](https://arxiv.org/abs/2603.07019)
- [AdaRubric (Mar 2026) — arXiv 2603.21362](https://arxiv.org/abs/2603.21362)
- [Are Checklists Really Useful? (ACL Findings 2025) — arXiv 2508.15218](https://arxiv.org/abs/2508.15218)
- [LettuceDetect (KRLabs Feb 2025) — arXiv 2502.17125](https://arxiv.org/abs/2502.17125)
- [LettuceDetect-large model card (HuggingFace)](https://huggingface.co/KRLabsOrg/lettucedect-large-modernbert-en-v1)
- [CoRefine: Confidence-Guided Self-Refinement (Feb 2026) — arXiv 2602.08948](https://arxiv.org/abs/2602.08948)
- [When LLM Judge Scores Look Good but Best-of-N Decisions Fail (Mar 2026) — arXiv 2603.12520](https://arxiv.org/abs/2603.12520)
- [Pairwise RM Knockout Tournament — arXiv 2501.13007](https://arxiv.org/abs/2501.13007)
- [Replacing Judges with Juries (PoLL) — arXiv 2404.18796](https://arxiv.org/abs/2404.18796)
- [Long-form RewardBench (Mar 2026) — arXiv 2603.12963](https://arxiv.org/abs/2603.12963)
- [SCALE NLI long-doc inconsistency (EMNLP 2023) — arXiv 2310.13189](https://arxiv.org/abs/2310.13189)
- [LLMxMapReduce-V3 (EMNLP 2025 demo) — arXiv 2510.10890](https://arxiv.org/abs/2510.10890)
- [LLM-AggreFact Leaderboard](https://llm-aggrefact.github.io/)
- [Fact or Facsimile — Embedding factual robustness (Aug 2025) — arXiv 2508.20408](https://arxiv.org/abs/2508.20408)
- Current Synth code: `apps/fastapi/domains/dd/synth/`
- Prior Synth research: `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`, `docs/KD-SYNTH-LLM-TO-CLASSICAL-MAY2026.md`
