# Study Generator — Adaptive Learning Pipeline (State-of-the-Art, 2026)

> **⚠️ SUPERSEDED 2026-04-19.** Canonical design is now [`KNOWLEDGE-DISTILLER-ARCHITECTURE.md`](./KNOWLEDGE-DISTILLER-ARCHITECTURE.md), which keeps the Adaptive Grader and Self-Refine loop from this doc but adds: tiered ingestion (llms-full.txt → Context7 → Crawl4AI), dynamic chapter count (4–12), pedagogy artifacts (challenges.md + flashcards.json), and a model swap off the deprecated GLM-5.

> Production-grade autonomous study material generator featuring Self-Refine/Reflexion iterative improvement, LLM-as-Judge multi-dimensional evaluation, and epigraphic memory. Replaces static skill levels with adaptive content calibration.

**Status:** Historical — validation confirmed grader & Self-Refine pattern; see canonical doc for final architecture
**Core Paradigm:** Evaluate output quality + human preference + market context — not assumed level

---

## Executive Summary: The Adaptive Grader

**Key Innovation: No Static Levels**

| Static "Advanced" | Adaptive Grader |
|------------------|-----------------|
| User claims "Advanced" | System evaluates output across 8 dimensions |
| Skip "basics" (which ones?) | Calibrates to user's mastered technologies from memory |
| Generic for all frameworks | Adapts to THIS framework's conceptual curve |
| Pre-defined market context | Injects target_markets (UAE G42/Stargate, Singapore DBS/Grab) |
| Generic "Money Projects" | Tailored to user's portfolio (COELHO RealTime/Agents) |

**Research-Validated Components:**
- **Self-Refine** (Madaan et al.): "20% avg improvement via critique-and-revise loops"
- **Reflexion** (Shinn et al.): Persistent verbal reinforcement learning
- **LLM-as-Judge** (zylos.ai): "50%+ production teams use runtime quality gating"
- **RAGAS + DeepEval**: 2026 production standard for multi-dimensional evaluation
- **AMA**: Adaptive Memory via Multi-Agent (arXiv:2601.20352)

---

## 1. High-Level Architecture

### The 6-Step Adaptive Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                           USER REQUEST                              │
│  Input: framework + version + user_profile (episodic memory)       │
│         target_markets, mastered_technologies, portfolio_refs      │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 1: DISCOVERY AGENT (Crawl4AI + LLMExtractionStrategy)          │
│ ├─ Find all docs URLs via sidebar/sitemap crawling                  │
│ ├─ Tag by section: quickstart, api-reference, how-to, migration     │
│ └─ Output: research/manifest.md (complete URL inventory)           │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 2: FETCHER AGENT (Crawl4AI arun_many, no LLM)                 │
│ ├─ Parallel async fetch: asyncio.gather with semaphore=5            │
│ ├─ Write: research/raw/<slug>.md (one per docs page)                  │
│ └─ Rate limit: Reserves NIM budget for synthesis/evaluation          │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 3: PLANNER AGENT (Kimi K2.5, structured output)                │
│ ├─ Assign raw files to 8 chapters per content semantics             │
│ ├─ Output: research/plan.json (chapter → [file_slugs])                │
│ └─ Adapts: chapter structure to framework complexity                │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 4: PARALLEL SYNTHESIS + ADAPTIVE GRADER (Self-Refine Loop)    │
│                                                                      │
│  ┌──────────────┐    ┌─────────────┐    ┌──────────────┐          │
│  │ SYNTHESIZER  │───▶│    GRADER   │───▶│  ADJUSTMENT  │          │
│  │ (GLM-5.1)    │    │ (8 dim eval │    │  GENERATOR   │          │
│  │              │    │ Kimi K2.5)  │    │              │          │
│  └──────────────┘    └─────────────┘    └──────┬───────┘          │
│         ▲                                      │                   │
│         │         score < 0.85?                │                   │
│         └──────────────────────────────────────┘                   │
│                                                                      │
│  • 8 concurrent chapters via LangGraph Send()                       │
│  • Max 2 refinement iterations                                       │
│  • Specific adjustments (not generic "improve")                    │
│  • Output: research/synth/chNN.md + chapterNN/README.md              │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 5: CRITIC AGENT (Nemotron-3-Nano + RAGAS)                     │
│ ├─ Faithfulness: claims supported by raw docs?                       │
│ ├─ Citation integrity: # docs: traces exist?                        │
│ ├─ Code compile: syntax validation                                  │
│ └─ Output: validation_report.json + per-chapter scores               │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 6: ASSEMBLER AGENT (GLM-5.1)                                   │
│ ├─ Cross-reference all chapters for consistency                      │
│ ├─ Output: summary.md (index + market roadmap + earning potential)   │
│ ├─ Output: DEBT.md (TODOs from grader + critic)                     │
│ └─ Episodic update: Store learnings for next study                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. The Adaptive Grader Agent

### 2.1 Core Concept

**Instead of:** Pre-classifying user as "Advanced"  
**System does:** Evaluate actual synthesis output quality

```python
class AdaptiveGraderEvaluation(BaseModel):
    """
    Multi-dimensional output quality assessment.
    Replaces static personas with actual performance metrics.
    """
    
    # DIMENSION 1: Signal-to-Noise (Anti-Padding)
    signal_to_noise: float
    # Target: 0.8+ for "no padding" preference
    # Tracks: intros, "we will learn...", summaries, motivational language
    
    # DIMENSION 2: Assumption Calibration
    assumption_match: float
    # Evaluates: Are we explaining Docker to a Kubernetes expert?
    # Checks: user.episodic_memory.framework_mastery
    
    # DIMENSION 3: Market Relevance
    job_alignment: float
    # Evaluates: Does content target user's target_markets?
    # UAE: G42, Stargate, Emirates NBD mentions
    # Singapore: DBS, Grab, GovTech alignment
    
    # DIMENSION 4: Citation Integrity
    citation_integrity: float
    # RAGAS: faithfulness metric
    # Every API call has # docs: <file> reference?
    
    # DIMENSION 5: Code Density
    code_density: float
    # Target: 60%+ code lines for senior engineers
    
    # DIMENSION 6: Portfolio Synergy
    portfolio_synergy: float
    # Evaluates: Links to COELHO RealTime/Agents/GraphRAG?
    
    # DIMENSION 7: Framework Complexity Match
    complexity_appropriate: float
    # Evaluates: Material depth matches framework's conceptual load
    # CUDA: needs theory | LangGraph: API-heavy
    
    # DIMENSION 8: Monetization Potential
    market_analysis: float
    # Evaluates: Money projects actually monetizable in target geo?
    
    # Decision
    weighted_score: float  # 0.0-1.0 composite
    action: Literal["accept", "refine_density", "refine_market", "refine_assumptions", "regenerate"]
    adjustment_prompt: str  # Specific, actionable instructions for retry
```

### 2.2 Self-Refine Loop (Research: 20% avg improvement)

```python
async def synthesize_with_calibration(payload: dict, config: dict) -> dict:
    """
    Self-Refine pattern: Synthesize → Grade → Adjust → (Retry)
    Max 2 iterations, specific adjustments.
    """
    trajectory = []
    
    for iteration in range(3):  # 0, 1, 2 (max 2 retries)
        # Build prompt with previous adjustments
        synthesizer_prompt = build_adaptive_prompt(
            base=get_template_for_user(config.user_profile),
            raw_docs=await read_files(payload.assigned_files),
            previous_adjustments=[t["adjustment"] for t in trajectory]
        )
        
        # Generate (GLM-5.1 for code-heavy content)
        synthesis = await config.synthesizer_llm.ainvoke(synthesizer_prompt)
        
        # Grader evaluates (Kimi K2.5 for nuanced judgment)
        grader = AdaptiveGrader(config.episodic_memory)
        evaluation = await grader.evaluate(
            synthesis_text=synthesis.content,
            user_profile=config.user_profile,
            framework=payload.framework
        )
        
        # Track trajectory (Reflexion learning)
        trajectory.append({
            "iteration": iteration,
            "synthesis": synthesis.content,
            "evaluation": evaluation,
            "score": evaluation.weighted_score
        })
        
        # Decision
        threshold = config.user_profile.acceptance_threshold  # 0.85 default
        if evaluation.weighted_score >= threshold:
            await write_synthesis(synthesis.content, payload)
            await update_episodic_memory(config.user_id, trajectory)
            return {"status": "accepted", "score": evaluation.weighted_score}
        
        if iteration < 2:
            # Generate specific adjustment
            adjustment = grader.generate_adjustment(evaluation, trajectory)
            print(f"  Adjusting ch{payload.chapter}: {evaluation.specific_issues[0]}")
            continue
        else:
            # Max retries: accept with DEBT flag
            await write_synthesis(synthesis.content, payload)
            await write_debt_file(trajectory, payload)
            return {"status": "debt_accepted", "score": evaluation.weighted_score}
```

### 2.3 Adjustment Generator (Specific, Not Generic)

```python
def generate_adjustment(self, evaluation: GraderEvaluation, trajectory: list) -> str:
    """
    Convert evaluation failures into specific synthesizer instructions.
    Example: Not "improve quality" but "Remove 'In this chapter...' intros"
    """
    adjustments = []
    
    # Example: Rafael's specific requirements
    if evaluation.signal_to_noise < 0.7:
        adjustments.append("""
        [PADDING] User explicitly specified NO PADDING.
        REMOVE: 'In this chapter...', 'We will learn...', 'Summary'
        ACTION: Start every section with code. Context AFTER code.
        """")
    
    if evaluation.assumption_match < 0.6:
        mastered = self.user_memory.get_mastered_technologies()[:10]
        adjustments.append(f"""
        [ASSUMPTIONS] User knows: {mastered}
        SKIP: Explaining these at intro level
        FOCUS: Framework-specific differences
        """")
    
    if evaluation.job_alignment < 0.7 and self.user_profile.target_markets[0] == "uae":
        adjustments.append("""
        [MARKET] User targeting UAE G42/Stargate
        ADD: Arabic NLP premium mention, G42 deployment patterns
        REFERENCE: Stargate UAE $30B campus positioning
        """")
    
    if evaluation.portfolio_synergy < 0.6:
        adjustments.append("""
        [PORTFOLIO] Link to COELHO RealTime and COELHO Agents
        EXAMPLE: 'This integrates with your Kafka + DeepSeek flow'
        """")
    
    return "\n\n".join(adjustments)
```

---

## 3. Episodic Memory (Trajectory Learning)

**Research:** AMA pattern (arXiv:2601.20352) + Microsoft Foundry

```python
class EpisodicMemory(BaseModel):
    """
    Learned preferences from completed studies (not static).
    Updates after each synthesis trajectory.
    """
    
    user_id: str
    
    # Observed Preferences (learned, not stated)
    observed_code_density: float = 0.65  # Exponential moving average
    observed_padding_tolerance: float = 0.1  # Low = strict no-padding
    
    # Technology Mastery Credential
    framework_mastery: dict[str, MasteryRecord]
    # {"kubernetes": {"level": "expert", "patterns": ["operators", "sidecars"]}}
    
    # Winning Adjustments
    successful_adjustments: list[dict]
    # [{"issue": "padding", "fix": "REMOVE intros", "success_rate": 0.95}]
    
    # Market References
    effective_market_hooks: dict[str, list[str]]
    # {"uae": ["stargate", "g42", "arabic_nlp"], "singapore": ["dbs", "grab"]}
    
    async def update_from_trajectory(self, trajectory: list, framework: str):
        """Reflexion: Learn from synthesis attempts"""
        final = trajectory[-1]
        
        # Update code density preference
        density = calculate_code_density(final["synthesis"])
        self.observed_code_density = 0.7 * self.observed_code_density + 0.3 * density
        
        # Log successful adjustments
        if final["score"] >= 0.9:
            for issue in final["evaluation"].specific_issues:
                self.successful_adjustments.append({
                    "framework": framework,
                    "issue": issue,
                    "fix": extract_fix(trajectory),
                    "success_rate": 1.0
                })
```

---

## 4. Research Citations

| Component | Research Source | Finding |
|-----------|-----------------|---------|
| **Self-Refine** | Madaan et al. | "20% avg improvement via iterative refinement" |
| **Reflexion** | Shinn et al. | "Verbal reinforcement learning from evaluation" |
| **LLM-as-Judge** | zylos.ai 2026 | "50%+ production teams use runtime quality gating" |
| **AMA** | arXiv:2601.20352 | "Multi-agent memory decomposition" |
| **RAGAS** | 2026 Standard | "Multi-dimensional RAG evaluation" |
| **DeepEval** | Confident AI | "Unit-test-style LLM evaluation" |
| **Send() Parallel** | LangGraph | "70% faster than sequential" |

---

## 5. Why This is State-of-the-Art

| Feature | Static "Advanced" | Adaptive Grader |
|---------|------------------|-----------------|
| **Personalization** | One-size-fits-all | Learns YOUR density preference |
| **Assumptions** | Skip "basics" (which?) | Calibrates to YOUR mastered tech |
| **Framework Matching** | Same depth all frameworks | Adapts to conceptual complexity |
| **Market Context** | Generic | Injects YOUR target_markets |
| **Portfolio** | Generic | Links to YOUR published projects |
| **Iterative** | None | Self-Refine up to 2x |
| **Learning** | Static | Episodic memory improves over time |

**Result:** Material calibrated to Rafael's specific needs: maximum density, skips mastered tech (K8s, FastAPI, LangGraph), targets UAE G42/Stargate and Singapore DBS/Grab, links to COELHO RealTime/Agents/GraphRAG portfolios.

---

*Document:** STUDY-GENERATOR-ARCHITECTURE-FINAL.md*  
*Version:* 2026-04-19-State-of-the-Art  
*Status:* Ready for implementation
