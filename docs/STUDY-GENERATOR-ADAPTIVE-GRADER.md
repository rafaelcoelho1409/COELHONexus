# Adaptive Grader Agent — Dynamic Content Calibration

> No static levels. No beginner/intermediate/advanced. Instead: **Evaluate → Adjust → Refine** based on what THIS user needs for THIS framework.

---

## Core Insight

Static personas fail because:
- **"Advanced"** for LangGraph ≠ **"Advanced"** for CUDA - different learning curves
- **Your** "Advanced" ≠ **my** "Advanced" - you've done 6 K8s studies, I just deployed my first cluster
- Frameworks change fast - 2024 "Advanced" patterns are 2026 "_deprecated"

**The Grader Agent evaluates actual output** against:
1. What this framework requires (docs analysis)
2. What this user already knows (episodic memory)
3. What this user is targeting (UAE hiring vs. personal growth)
4. What "quality" means for senior engineers

Then **adjusts** - not just passes/fails.

---

## Grader Agent Architecture

### 1. Evaluation Dimensions

```python
class GraderEvaluation(BaseModel):
    """Multi-dimensional evaluation of synthesis quality"""
    
    # Dimension 1: Density (Anti-Padding)
    signal_to_noise: float  # 0.0-1.0
    # "Maximum density" = no intros, no "we will learn", just code + essential context
    
    # Dimension 2: Assumption Calibration
    assumption_match: float  # 0.0-1.0
    # Are we explaining "what is a container" to a K8s expert?
    # Are we assuming knowledge of "attention mechanisms" with a new NLP framework?
    
    # Dimension 3: Market Relevance
    job_alignment: float  # 0.0-1.0
    # Does this framework skill appear in UAE/Singapore/US job descriptions?
    # Is the "Money Projects" chapter actually monetizable?
    
    # Dimension 4: Verification Quality
    citation_integrity: float  # 0.0-1.0
    # Every API call traceable to a raw docs file?
    # No hallucinations of classes/params that don't exist?
    
    # Dimension 5: Code-First Ratio
    code_density: float  # 0.0-1.0
    # Lines of code / total lines. Target: 60%+ for senior engineers
    
    # Dimension 6: Portfolio Integration
    portfolio_synergy: float  # 0.0-1.0
    # Does chapter07 connect to COELHO RealTime/flagship projects?
    # Or is it generic "hello world" stuff?
    
    # Dimension 7: Framework Complexity Match
    complexity_appropriate: float  # 0.0-1.0
    # Some frameworks need deep theory (transformers internals)
    # Others are API-heavy (LangChain wrappers)
    # Did we match the material depth to the framework's conceptual load?
    
    weighted_score: float  # Composite 0.0-1.0
    action: Literal["accept", "refine_depth", "refine_density", "refine_market", "regenerate"]
    specific_issues: list[str]
    adjustment_prompt: Optional[str]  # Injected into synthesizer on retry
```

### 2. The Calibration Loop

```
SYNTHESIZE
    ↓
GRADER EVALUATES (8 dimensions)
    ↓
IF score < threshold:
    ↓
GENERATE ADJUSTMENT PROMPT
    ↓
REGENERATE WITH ADJUSTMENT
    ↓
GRADER RE-EVALUATES
    ↓ (max 2 iterations)
ACCEPT → Store final version
```

### 3. Dynamic Adjustment Examples

**Scenario A: Too much padding** (Rafael's complaint)
```
Evaluation: signal_to_noise = 0.4
Issues: ["Chapter starts with 'In this chapter we will...'",
         "Each section has 3-paragraph intro before code"]
         
Adjustment Prompt:
"CRITICAL: User specified NO PADDING.
Remove ALL intros, summaries, "we will learn..."
Every new section must START with code.
Context comes AFTER code, maximum 2 sentences.
Target: 80%+ code density"
```

**Scenario B: Wrong assumptions** (Zod framework)
```yaml
# User profile shows 5 K8s studies
# Current chapter explains "what is a container"

Evaluation: assumption_match = 0.2
Issues: ["Explaining containerization to Kubernetes expert",
         "Wasting 500 words on Docker basics"]

Adjustment Prompt:
"CONTEXT: User has extensive Kubernetes experience.
SKIP: containerization basics, Docker fundamentals, "why containers"
ASSUME: User knows cgroups, namespaces, overlayfs, multi-stage builds
FOCUS: Zod-specific container patterns, Zod+K8s quirks"
```

**Scenario C: Market misalignment** (UAE targeting)
```yaml
# Chapter mentions "US-only cloud providers"
# No mentions of G42, Emirates NBD, Stargate UAE

Evaluation: job_alignment = 0.3
Issues: ["Mentions AWS/GCP but ignores UAE sovereign cloud (G42 Cloud)",
         "Money projects don't reference UAE banking regulations",
         "No Arabic NLP integration (massive UAE premium)"]

Adjustment Prompt:
"CRITICAL: User targeting UAE market (G42, Emirates NBD).
REQUIRED INCLUSIONS:
- G42 Cloud deployment patterns
- Arabic NLP integration (Jais model)
- UAE data residency requirements
- Emirates NBD / FAB fintech use cases
STARGATE UAE: New $30B AI campus - frame as top of market positioning"
```

**Scenario D: Citation gaps**
```yaml
# Code shows `new_api_call()` with no docs reference

Evaluation: citation_integrity = 0.5
Issues: ["Line 47: uses `model.compile()` with no docs citation",
         "Line 112: claims 'default is 5s' - verify in raw/defaults.md"]

Adjustment Prompt:
"VERIFICATION REQUIRED: Every API call needs citation comment.
Line 47: Add `# docs: Compilation API (research/raw/core-api.md)`
Line 112: Verify claim against research/raw/defaults.md
Add citation or mark # TODO if unverified."
```

---

## Implementation

### Phase 1: Grader Node

```python
# graphs/study_pipeline/nodes/grader.py

class AdaptiveGrader:
    """
    Evaluates synthesis quality and generates adjustment prompts.
    No static levels - evaluates actual output against actual needs.
    """
    
    def __init__(self, user_memory: EpisodicMemory, llm: BaseChatModel):
        self.user_memory = user_memory  # Past studies, mastered tech
        self.evaluator_llm = llm  # Kimi K2.5 or Claude for evaluation
    
    async def evaluate_synthesis(
        self,
        synthesis_text: str,
        assigned_files: list[str],
        framework: str,
        user_context: UserContext
    ) -> GraderEvaluation:
        """
        8-dimensional evaluation of chapter synthesis.
        """
        
        # Build evaluation prompt with all context
        eval_prompt = self._build_evaluation_prompt(
            synthesis_text=synthesis_text,
            assigned_files=assigned_files,
            framework=framework,
            user_context=user_context
        )
        
        # LLM evaluates across all dimensions
        response = await self.evaluator_llm.with_structured_output(
            GraderEvaluation
        ).ainvoke(eval_prompt)
        
        return response
    
    def _build_evaluation_prompt(
        self,
        synthesis_text: str,
        assigned_files: list[str],
        framework: str,
        user_context: UserContext
    ) -> str:
        return f"""You are the Grader Agent for an adaptive study system.

THE USER (NOT a "level", a specific person):
- Current role: {user_context.current_role}
- Years experience: {user_context.years_experience}
- Previously studied: {', '.join(user_context.mastered_technologies)}
- Target markets: {', '.join(user_context.target_markets)}
- Portfolio: {user_context.portfolio_url}
- Study preferences:
  * Explanation depth: {user_context.explanation_depth} (concise vs detailed)
  * Code-first: {user_context.code_first}
  * No padding: {user_context.no_padding}
  * Target: {user_context.target_pages_per_chapter} pages per chapter

THE FRAMEWORK:
- Name: {framework}
- Documentation comprehensiveness: {self._analyze_framework_complexity(framework)}

SYNTHESIZED CHAPTER:
```
{synthesis_text[:10000]}  # Truncated for token limit
```

ASIGNED SOURCE FILES (user expects coverage of these):
{chr(10).join(f"- {f}" for f in assigned_files)}

EVALUATE ACROSS 8 DIMENSIONS:
1. [signal_to_noise] Is it maximum density? Or padded with intros/summaries?
2. [assumption_match] Right complexity for {user_context.years_experience} year engineer?
3. [job_alignment] Does it serve {user_context.target_markets[0]} job market?
4. [citation_integrity] Every claim has # docs: citation?
5. [code_density] 60%+ code lines for senior level?
6. [portfolio_synergy] Connects to {user_context.portfolio_url.split('/')[-2]} flagship projects?
7. [complexity_appropriate] Matches this framework's conceptual load?
8. [market_analysis] Money projects actually monetizable in target markets?

SCORE EACH 0.0-1.0, then weighted composite.
If composite < 0.85, specify:
- What fundamental issue exists
- Specific adjustment prompt to inject on regeneration"""
```

### Phase 2: Adjustment Generator

```python
    def generate_adjustment(
        self,
        evaluation: GraderEvaluation,
        iteration: int
    ) -> str:
        """
        Converts evaluation failures into synthesizer instructions.
        Not generic "be better" - specific, actionable adjustments.
        """
        
        if iteration >= 2:
            # Max retries reached - accept with warnings
            return None
        
        adjustments = []
        
        # Dimension 1: Padding
        if evaluation.signal_to_noise < 0.7:
            adjustments.append(
                "[PADDING] User specified NO PADDING. "
                "Remove ALL: 'In this chapter...', 'We will learn...', 'Summary', 'Conclusion'. "
                "Start every section with code. Context AFTER code, max 2 sentences."
            )
        
        # Dimension 2: Assumptions
        if evaluation.assumption_match < 0.6:
            known = ", ".join(evaluation.user_context.mastered_technologies[:10])
            adjustments.append(
                f"[ASSUMPTIONS] User knows: {known}. "
                "SKIP: basics of these. FOCUS: integration with this framework."
            )
        
        # Dimension 3: Market relevance
        if evaluation.job_alignment < 0.7:
            market = evaluation.user_context.target_markets[0]
            if market == "uae":
                adjustments.append(
                    "[MARKET] User targeting UAE. "
                    "ADD: G42 mention, Arabic NLP integration, Emirates NBD use case, "
                    "UAE data residency, comparison with Stargate UAE positioning."
                )
            elif market == "singapore":
                adjustments.append(
                    "[MARKET] User targeting Singapore. "
                    "ADD: DBS/Grab use cases, SEA-specific patterns, "
                    "GovTech alignment, ONE Pass Jan 2027 reference."
                )
        
        # Dimension 4: Citations
        if evaluation.citation_integrity < 0.8:
            adjustments.append(
                "[CITATIONS] Every API call needs verification comment. "
                "Format: # docs: <section> (research/raw/<file.md>). "
                "No citation = hallucination risk. Mark unverified with # TODO: verify."
            )
        
        # Dimension 5: Code density
        if evaluation.code_density < 0.5:
            adjustments.append(
                "[DENSITY] Target 60%+ code. User prefers code-first, explanation-after. "
                "Reduce prose by 50%."
            )
        
        # Dimension 6: Portfolio
        if evaluation.portfolio_synergy < 0.6:
            projects = "COELHO RealTime, COELHO Agents, YouTube Content Search"
            adjustments.append(
                f"[PORTFOLIO] Link chapter07 to: {projects}. "
                "Show how this extends existing work, not standalone tutorial."
            )
        
        # Combine into prompt injection
        adjustment_prompt = "\n\n".join(
            f"ADJUSTMENT {i+1}: {adj}" 
            for i, adj in enumerate(adjustments)
        )
        
        return adjustment_prompt
```

### Phase 3: Regeneration Loop

```python
# In the synthesizer node
async def synthesize_with_calibration(
    payload: dict,
    config: dict
) -> dict:
    """
    Synthesize → Grade → Adjust → (Retry if needed)
    """
    from langchain_core.prompts import ChatPromptTemplate
    
    max_iterations = 2
    adjustment_history = []
    
    for iteration in range(max_iterations + 1):
        # Build prompt with adjustments from previous runs
        synthesizer_prompt = build_synthesizer_prompt(
            chapter=payload["chapter_num"],
            framework=payload["framework"],
            raw_docs=await read_assigned_files(payload),
            adjustments=adjustment_history
        )
        
        # Generate synthesis
        synthesis = await config["llm"].ainvoke(synthesizer_prompt)
        synthesis_text = synthesis.content
        
        # Grade the result
        grader = AdaptiveGrader(
            user_memory=config["user_memory"],
            llm=config["grader_llm"]
        )
        
        evaluation = await grader.evaluate_synthesis(
            synthesis_text=synthesis_text,
            assigned_files=payload["assigned_files"],
            framework=payload["framework"],
            user_context=config["user_context"]
        )
        
        # Decide action
        if evaluation.weighted_score >= 0.85:
            # Accept
            await write_synthesis_to_disk(synthesis_text, payload)
            return {"status": "accepted", "score": evaluation.weighted_score}
        
        if iteration < max_iterations:
            # Generate adjustment for retry
            adjustment = grader.generate_adjustment(evaluation, iteration)
            adjustment_history.append(adjustment)
            print(f"  Chapter {payload['chapter_num']}: Adjusting for retry {iteration + 1}")
            print(f"  Issue: {evaluation.specific_issues[0]}")
        else:
            # Max retries - accept with debt
            await write_synthesis_to_disk(synthesis_text, payload)
            await write_debt_file(evaluation, payload)
            return {"status": "debt", "score": evaluation.weighted_score}
    
    return {"status": "error"}
```

---

## Key Advantages Over Static Levels

| Static Level | Adaptive Grader |
|--------------|-----------------|
| "Advanced" = explainer: false | Evaluates actual output density |
| "Intermediate" = skip basics | Checks user knowledge epigraphically |
| Generic for all frameworks | Calibrates to THIS framework's complexity |
| One-size-fits-all | User's 6 years K8s = skip container theory |
| Pre-defined target markets | Injects real UAE/Singapore/US context |

**For Rafael specifically:**

The grader would learn:
- Already mastered: K8s, FastAPI, LangChain, LangGraph
- Style preference: Maximum density, code-first
- Target: UAE G42/Stargate, Singapore DBS/Grab
- Portfolio: RealTime, Agents, GraphRAG

So when synthesizing "DeepSeek v3":
- Chapter 1 (Setup): Skip "what is an LLM", focus on DeepSeek-specific quirks
- Chapter 2 (Core): Assume PyTorch knowledge, compare to transformers
- Chapter 6 (Integration): Connect to COELHO RealTime Kafka + DeepSeek inference
- Chapter 8 (Money): UAE pricing/privacy requirements + G42 deployment
- Chapter 7 (Anti-patterns): Don't explain what errors are - show DeepSeek-specific gotchas

---

## User Feedback Loop

After completing a study, the grader updates the user's "episodic memory":

```python
class EpisodicMemory(BaseModel):
    """
    Learned preferences from completed studies.
    Not static profile - dynamic, refined over time.
    """
    
    # What we've learned about this user
    observed_code_density_preference: float  # 0.3 - 0.9
    actual_explanation_depth: Literal["minimal", "concise", "detailed"]
    # (may differ from what they claimed)
    
    # Framework-specific insights
    frameworks_mastered: dict[str, FrameworkMastery]
    # e.g., {"kubernetes": {"mastery_level": "expert", "known_patterns": [...]}}
    
    # Market positioning learned
    effective_market_hooks: dict[str, list[str]]
    # e.g., {"uae": ["stargate", "g42", "emirates_nbd"]}
    
    # What triggered regressions
    adjustment_patterns: list[dict]
    # e.g., [{"issue": "padding", "trigger": <0.7 score, "fix": "..."}]

# After each study completion
async def update_episodic_memory(
    user_id: str,
    completed_study: StudyState,
    final_scores: dict[int, float]  # per-chapter scores
):
    memory = await load_memory(user_id)
    
    # Update code density preference
    avg_density = calculate_avg_code_density(completed_study)
    memory.observed_code_density_preference = (
        memory.observed_code_density_preference * 0.7 + 
        avg_density * 0.3  # Weighted update
    )
    
    # Log what worked
    for chapter_num, score in final_scores.items():
        if score >= 0.9:
            memory.success_patterns.append({
                "framework": completed_study.framework,
                "chapter": chapter_num,
                "what_worked": extract_winning_patterns(completed_study)
            })
    
    await save_memory(memory)
```

**Result:** Each study makes the grader smarter for the next one.

---

## Usage Flow

```bash
# First study - grader learns your style
POST /studies (DuckDB)
  ↓
Chapter 1: First draft too padded (0.6 density score)
  ↓
Grader injects: "NO PADDING" adjustment
  ↓
Retry: 0.85 score (acceptable)
  ↓
Store: "User rejects padding, prefers max density"

# Second study (LangGraph)
  ↓
Grader pre-injects: "NO PADDING" from learned preference
  ↓
First draft: 0.88 score (passes first try)

# Third study (CUDA)
  ↓
Grader knows: Max density, assume PyTorch expert
  ↓
But CUDA needs theory (different framework)
  ↓
Grader evaluation: "Signal good, but missing GPU memory theory"
  ↓
Adjustment: "ADD: Shared memory architecture figure" (not padding - essential for CUDA)
  ↓
Retry: 0.92 score
```

**The grader learns YOU, not a generic persona.**
