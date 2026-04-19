# Study Generator Architecture Reference (2026 Update)

> **⚠️ SUPERSEDED 2026-04-19.** Canonical design is now [`KNOWLEDGE-DISTILLER-ARCHITECTURE.md`](./KNOWLEDGE-DISTILLER-ARCHITECTURE.md). This document remains a useful **technical reference** for DeepAgents patterns, LangGraph `Send()` mechanics, and Crawl4AI v0.8 API calls — those specifics are still accurate and are cited from the canonical doc.

> Complete technical specification for the COELHO Nexus Study Generator, incorporating DeepAgents v0.5.3, LangGraph parallel execution patterns, and Crawl4AI v0.8 best practices from April 2026.

---

## Executive Summary

The Study Generator is an **autonomous compiler for documentation** - transforming raw framework docs into structured, senior-level study materials through a 6-step parallel pipeline.

**Key Architectural Decisions (April 2026):**

| Decision | Technology | Rationale |
|----------|------------|-----------|
| Agent Harness | DeepAgents v0.5.3 | Planning tools, sub-agents, filesystem operations built-in |
| Parallel Execution | LangGraph `Send()` API | Dynamic fan-out for 8 concurrent chapter synthesizers |
| State Accumulation | `Annotated[list, operator.add]` | Thread-safe result aggregation across parallel agents |
| Documentation Crawl | Crawl4AI v0.8.x | LLM-friendly Markdown, adaptive crawling, anti-bot |
| Multi-Model Routing | Kimi K2.5 + GLM-5.1 + Nemotron | 40 RPM per model on NIM free tier = 160 RPM aggregate |
| Persistence | `AsyncPostgresSaver` | Durable execution across server restarts, job-resumable |
| Quality | RAGAS-style + LLM-as-Judge | Citation verification, claim-to-source validation |

---

## 1. The Compiler Analogy (Karpathy's LLM Wiki Pattern)

The architecture follows Andrej Karpathy's "compiler" mental model for knowledge bases:

```
RAW INPUT        COMPILATION           COMPILED OUTPUT
framework docs   →  6-step pipeline  →  study/
├── metadata     →  discovery          ├── manifest.md
├── URLs         →  fetch              ├── research/raw/
├── HTML         →  planner            ├── research/plan.json
├── raw md       →  synthesize         ├── research/synth/chNN.md
├── chunked      →  critic (validate)  ├── chapterNN/README.md
└── synthesized  →  assemble           ├── summary.md
                                           └── DEBT.md
```

**Key Insight:** Like source code compilation, each phase produces intermediate artifacts that downstream phases consume. This enables:
- **Debugging**: Inspect any phase's output
- **Resumability**: Resume from the last completed phase
- **Validation**: Each phase has quality gates

---

## 2. DeepAgents Integration (v0.5.3)

DeepAgents is LangChain's "agent harness" - an opinionated framework providing:

### 2.1 Built-in Tools

```python
from deepagents import create_deep_agent, planning, filesystem, task_tool

# Planning: write_todos - breaks complex tasks into manageable steps
todos = planning.write_todos([
    "Discover all documentation URLs",
    "Extract sidebar navigation",
    "Tag URLs by section type"
])

# Filesystem: read_file, write_file, edit_file, ls, glob, grep
filesystem.write_file(
    path="/studies/duckdb/research/manifest.md",
    content=manifest_table
)

# Task Tool: Delegate work to sub-agents with isolated context
task_tool.spawn(
    agent="synthesizer-ch01",
    input={"chapter": 1, "files": assigned_files}
)
```

### 2.2 Sub-Agent Pattern

DeepAgents provides structured sub-agent delegation:

```python
from deepagents import Subagent

# Define specialized chapter synthesizers
chapter_synthesizer = Subagent(
    name="synthesizer-chapter",
    description="Synthesizes one chapter from assigned documentation",
    system_prompt="""You are a chapter synthesizer for senior engineers.
Rules:
- Code first, explanation after
- Every code block: `# docs: <section> (path/file.md)`
- No padding, no "in this chapter..."
- REAL USE CASES mapped to UAE/Singapore/US markets

Output:
1. research/synth/chNN.md (condensed notes)
2. chapterNN/README.md (complete guide)""",
    tools=[filesystem.read_file, filesystem.write_file, planning.write_todos],
)
```

### 2.3 Context Management

DeepAgents uses file-based context isolation - each sub-agent reads from/writes to its own scope, preventing cross-contamination.

---

## 3. LangGraph Parallel Execution (Send API)

The synthesis phase uses **dynamic fan-out** - the core innovation for parallel chapter generation.

### 3.1 Pattern: Send() for Parallel Sub-Agents

```python
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from typing import Annotated
import operator

class StudyState(TypedDict):
    # ... other fields
    chapters: dict[str, ChapterState]
    synthesis_results: Annotated[list[dict], operator.add]  # Key: reducer

workflow = StateGraph(StudyState)

# Step 1: Entry node (fans out to parallel workers)
def synthesize_entry(state: StudyState):
    """Returns empty - actual work in parallel"""
    return {}

# Step 2: Dynamic fan-out (runs 8 times, once per chapter)
def synthesize_fan_out(state: StudyState) -> list[Send]:
    """
    Creates 8 parallel invocations of 'synthesize_chapter'.
    Each Send targets the same node with different inputs.
    """
    sends = []
    for chapter_num, chapter_state in state["chapters"].items():
        sends.append(
            Send(
                "synthesize_chapter",  # Target node
                {                       # Input to node
                    "chapter_num": chapter_num,
                    "chapter_state": chapter_state,
                    "framework": state["framework"],
                }
            )
        )
    return sends

# Step 3: Individual chapter synthesizer (runs 8 times in parallel)
async def synthesize_chapter(payload: dict, config: dict) -> dict:
    """
    Single chapter worker.
    Called 8 times concurrently via Send().
    """
    chapter_num = payload["chapter_num"]
    files = payload["chapter_state"]["assigned_files"]
    
    # Read assigned docs
    content = await read_files(files)
    
    # Generate synthesis (using appropriate model)
    synthesis = await generate_synthesis(content, chapter_num)
    
    # Key: Return via operator.add reducer
    return {
        "synthesis_results": [{  # List element, merged automatically
            "chapter": chapter_num,
            "synthesis": synthesis,
            "status": "complete"
        }]
    }

# Step 4: Merge node (waits for all 8 to complete)
def synthesize_merge(state: StudyState) -> dict:
    """Called only after all 8 synthesizers complete"""
    # Results automatically accumulated in synthesis_results
    by_chapter = {r["chapter"]: r for r in state["synthesis_results"]}
    return {"chapters": update_chapter_states(by_chapter)}

# Graph wiring: fan-out → parallel execution → merge
workflow.add_node("synthesize_entry", synthesize_entry)
workflow.add_node("synthesize_chapter", synthesize_chapter)
workflow.add_node("synthesize_merge", synthesize_merge)

workflow.add_edge("synthesize_entry", "synthesize_fan_out")
workflow.add_conditional_edges(
    "synthesize_entry",
    synthesize_fan_out,           # Returns list[Send]
    ["synthesize_chapter"]      # All targets this node
)
workflow.add_edge("synthesize_chapter", "synthesize_merge")
```

### 3.2 The Reducer Pattern

The `operator.add` reducer is critical for parallel result accumulation:

```python
from typing import Annotated
import operator

class ParallelResults(TypedDict):
    """
    Without reducer: Each parallel result OVERWRITES the previous
    With operator.add: Each parallel result APPENDS to a list
    """
    results: Annotated[list[dict], operator.add]

# Node A returns: {"results": [{"id": 1}]}
# Node B returns: {"results": [{"id": 2}]}
# Final state:   {"results": [{"id": 1}, {"id": 2}]}
```

**Why This Matters for Study Generator:**
- 8 chapters can synthesize simultaneously
- No need to manage thread pools manually
- LangGraph handles the join automatically
- Results accumulated in deterministic order

---

## 4. State Schema Design

### 4.1 Primary State (StudyState)

```python
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from langgraph.graph.message import add_messages

PhaseStatus = Literal["pending", "running", "complete", "failed", "retrying"]

class ChapterState(BaseModel):
    """Per-chapter state - can be in different phases"""
    number: int = Field(..., ge=1, le=8, description="Chapter number 1-8")
    assigned_files: list[str] = []          # Files from planner
    status: PhaseStatus = "pending"
    critic_score: Optional[float] = None   # 0.0-1.0 from validation
    retry_count: int = Field(default=0, le=2)
    output_path: Optional[str] = None
    synthesis: Optional[str] = None         # Full synthesis output

class StudyState(BaseModel):
    """
    Root state for the 6-step pipeline.
    Persisted via AsyncPostgresSaver after every node.
    """
    # -- Input Configuration --
    framework: str                        # e.g., "langgraph"
    version: Optional[str] = None        # e.g., "0.2.0"
    target_docs_url: Optional[str] = None  # Starting URL
    study_root: str                       # Output directory
    
    # -- Phase Tracking --
    current_phase: Literal[
        "discovery", "fetch", "plan",
        "synthesize", "critic", "assemble",
        "complete", "failed"
    ] = "discovery"
    phase_status: dict[str, PhaseStatus] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # -- Discovery Outputs (Step 1) --
    manifest: list[dict] = Field(
        default_factory=list,
        description="List of doc URLs with metadata"
    )
    total_urls: int = 0
    
    # -- Fetcher Outputs (Step 2) --
    fetched_count: int = 0
    fetch_errors: list[str] = []
    
    # -- Planner Outputs (Step 3) --
    plan: dict[str, list[str]] = Field(
        default_factory=dict,
        description="chapter_num -> [file_slugs] mapping"
    )
    
    # -- Synthesizer Outputs (Step 4) --
    chapters: dict[str, ChapterState] = Field(
        default_factory=dict,
        description="Parallel chapter synthesis results"
    )
    
    # -- Critic Evaluation (Step 5) --
    validation_report: Optional[dict] = None
    overall_quality_score: Optional[float] = None
    
    # -- Assembler Outputs (Step 6) --
    summary: Optional[str] = None
    debt: Optional[str] = None
    
    # -- Observability --
    tokens_consumed: dict[str, int] = Field(
        default_factory=dict,
        description="Per-model token tracking"
    )
    wall_clock_seconds: float = 0.0
    
    # -- LangGraph required --
    messages: Annotated[list, add_messages] = []
```

### 4.2 Checkpointing Strategy

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# Each study gets a unique thread_id for resumability
thread_id = f"study:{framework}:{version}:{uuid}"
config = {"configurable": {"thread_id": thread_id}}

# State is persisted:
# - After every node completion
# - Can resume from any point on restart
# - Query status via checkpointer.get(config)
async with AsyncPostgresSaver.from_conn_string(PG_URL) as checkpointer:
    graph = build_study_pipeline(checkpointer=checkpointer)
    result = await graph.ainvoke(initial_state, config=config)
```

---

## 5. Multi-Model Routing Strategy

### 5.1 Model Assignment by Phase

Based on April 2026 benchmarks and the Iternal AI selection guide:

| Phase | Role | Model | Why |
|-------|------|-------|-----|
| Discovery | Orchestration | Kimi K2.5 | Agentic specialist, tool-calling, 256K context |
| Planner | Structuring | Kimi K2.5 | Complex reasoning, output formatting |
| Synthesis | Code Generation | GLM-5.1 | SWE-bench Pro #1 open-source (58.4%), MCP-Atlas 71.8% |
| Synthesis | Narrative | Kimi K2.5 | Mental models, comparisons |
| Critic | Verification | Nemotron-3-Nano | Cheap, fast, sufficient for validation |
| Assembly | Final Report | GLM-5.1 | Code-heavy synthesis |

### 5.2 Rate Limit Math (NVIDIA NIM Free Tier)

```
40 RPM per model (not per account!)
Using 4 different models:
- Kimi K2.5 (discovery, planning): 40 RPM
- GLM-5.1 (synthesis, assembly): 40 RPM  
- Nemotron-3-Nano (critic): 40 RPM
- Nemotron-3-Super (fallback): 40 RPM
= 160 RPM effective aggregate

With 8 concurrent chapters @ ~30s each:
- Peak: 8 parallel requests
- Sustained: Well within 160 RPM
```

### 5.3 LiteLLM Configuration

```yaml
# config/litellm_study.yaml
model_list:
  - model_name: study-discovery
    litellm_params:
      model: openai/moonshotai/kimi-k2.5
      api_base: https://integrate.api.nvidia.com/v1
      
  - model_name: study-synthesis
    litellm_params:
      model: openai/z-ai/glm-5.1
      api_base: https://integrate.api.nvidia.com/v1
      
  - model_name: study-critic  
    litellm_params:
      model: openai/nvidia/nemotron-3-nano-30b-a3b
      api_base: https://integrate.api.nvidia.com/v1

router_settings:
  routing_strategy: least-busy  # Load-balance across identical models
  num_retries: 3
  timeout: 600
```

---

## 6. Crawl4AI Integration (v0.8.x)

### 6.1 Key Capabilities

Crawl4AI v0.8.x provides LLM-friendly output:

| Feature | Benefit |
|---------|---------|
| `markdown.raw_markdown` | Full page as Markdown |
| `markdown.fit_markdown` | Boilerplate-stripped, focused content |
| `LLMExtractionStrategy` | Structured extraction via Pydantic |
| `BestFirstCrawlingStrategy` | Priority-based deep crawl |
| `AdaptiveCrawler.digest()` | Auto-stop when enough content gathered |
| `arun_many()` | Concurrent URL fetching |

### 6.2 Discovery Pattern

```python
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    LLMExtractionStrategy,
    LLMConfig
)
from pydantic import BaseModel

class DocURL(BaseModel):
    """Structured output for documentation discovery"""
    url: str
    slug: str
    section: Literal[
        "quickstart", "api-reference", "how-to",
        "integration", "migration", "advanced", "other"
    ]
    title: str
    
async def discover_docs(docs_root: str) -> list[DocURL]:
    """
    Extract all documentation URLs from sidebar/sitemap.
    Pattern: Crawl navigation pages only, use LLM to extract URLs
    """
    browser_config = BrowserConfig(
        headless=True,
        text_mode=True,  # Skip images, faster
    )
    
    run_config = CrawlerRunConfig(
        # Extract URL list from navigation
        extraction_strategy=LLMExtractionStrategy(
            llm_config=LLMConfig(
                provider="openai/moonshotai/kimi-k2.5",
                api_token=os.environ["NIM_API_KEY"],
            ),
            schema=DocURL.model_json_schema(),
            instruction="""
            Extract all documentation page URLs from the sidebar/navigation.
            Include:
            - Every link in the sidebar
            - Sitemap links
            - Table of contents
            
            Tag each by section:
            - quickstart: install, getting-started, hello-world
            - api-reference: api, sdk, reference, modules
            - how-to: guides, tutorials, examples
            - integration: connections, plugins, exporters
            - migration: upgrade, changelog, versioning
            - advanced: internals, architecture, contributing
            """,
        ),
        cache_mode=CacheMode.ENABLED,
    )
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url=docs_root,
            config=run_config
        )
        
        # Parse extracted structured data
        urls = []
        for item in result.extracted_content:
            urls.append(DocURL(**item))
            
        return urls
```

### 6.3 Concurrent Fetching

```python
async def fetch_all_docs(
    urls: list[DocURL],
    study_root: str,
    max_concurrent: int = 5
) -> list[dict]:
    """
    Fetch all documentation content in parallel.
    Rate-limited via semaphore to respect Crawl4AI/NIM limits.
    """
    import asyncio
    
    semaphore = asyncio.Semaphore(max_concurrent)
    browser_config = BrowserConfig(headless=True)
    
    async def fetch_one(url_obj: DocURL) -> dict:
        async with semaphore:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(
                    url=url_obj.url,
                    config=CrawlerRunConfig(
                        # Get clean, LLM-ready markdown
                        markdown=True,
                        content_filter=ContentFilter(cutoff=0.7),  # Remove boilerplate
                    )
                )
                
                # Write to file
                content = result.markdown.fit_markdown
                file_path = f"{study_root}/research/raw/{url_obj.slug}.md"
                await write_file(file_path, content)
                
                return {
                    "slug": url_obj.slug,
                    "success": True,
                    "size": len(content),
                }
    
    # Execute all fetches in parallel
    results = await asyncio.gather(*[
        fetch_one(url) for url in urls
    ])
    
    return results
```

---

## 7. Quality Validation (RAGAS-Style)

### 7.1 Critic Architecture

The critic validates synthesis quality through multiple lenses:

```python
class CriticAssessment(BaseModel):
    """Structured critic output"""
    citations_valid: bool               # All citations resolve
    hallucination_score: float          # 0-1, 1 = no hallucination
    code_completeness: float           # Do code blocks compile?
    coverage_score: float              # Covered assigned files?
    overall_score: float               # Weighted composite
    issues: list[str]                   # Specific problems found

async def critic_validate(
    state: StudyState,
    chapter: ChapterState,
    llm: BaseChatModel
) -> CriticAssessment:
    """
    RAGAS-style validation:
    1. Parse citations from synthesis
    2. Verify each citation exists in raw/
    3. Sample claims and verify against source
    4. Check code blocks for syntax errors
    """
    synthesis = await read_file(
        f"{state.study_root}/research/synth/ch{chapter.number:02d}.md"
    )
    
    # Extract citations: # docs: <section> (file.md)
    citations = extract_citation_pattern(synthesis)
    
    validation_results = []
    for citation in citations:
        # Verify file exists
        file_path = f"{state.study_root}/{citation['file']}"
        if not os.path.exists(file_path):
            validation_results.append({"citation": citation, "valid": False})
            continue
            
        # RAGAS: Verify claim appears in source
        source_content = await read_file(file_path)
        claim = get_surrounding_context(synthesis, citation)
        
        # LLM-as-Judge: Is claim supported by source?
        validation = await llm.ainvoke(verification_prompt.format(
            claim=claim,
            source=source_content[:5000]  # Truncated for context
        ))
        validation_results.append({
            "citation": citation,
            "valid": validation.content.strip().lower() == "supported"
        })
    
    citation_validity = sum(1 for r in validation_results if r["valid"]) / len(validation_results)
    
    return CriticAssessment(
        citations_valid=citation_validity > 0.95,
        hallucination_score=citation_validity,
        code_completeness=await validate_code_blocks(synthesis),
        coverage_score=await validate_coverage(synthesis, chapter.assigned_files),
        overall_score=calculate_weighted(citation_validity, ...),
        issues=[r["citation"] for r in validation_results if not r["valid"]]
    )
```

### 7.2 Retry Loop

Chapters failing validation (< 0.85) retry with feedback:

```python
def route_after_critic(state: StudyState) -> str:
    """Route: retry failing chapters or proceed to assembly"""
    needs_retry = []
    
    for chapter_num, chapter in state["chapters"].items():
        if chapter.status == "retrying":
            needs_retry.append(chapter_num)
    
    if needs_retry and any(
        state["chapters"][c].retry_count < 2 for c in needs_retry
    ):
        return "synthesize"  # Loop back
    
    return "assemble"  # Proceed
```

---

## 8. FastAPI Integration & Streaming

### 8.1 Job Submission Pattern

```python
@router.post("/studies")
async def create_study(
    request: StudyRequest,
    background_tasks: BackgroundTasks
) -> StudyResponse:
    """
    Submit study job.
    Returns immediately; job runs via LangGraph with checkpointing.
    """
    study_id = str(uuid4())
    thread_id = f"study:{request.framework}:{study_id}"
    
    # Initial state
    initial_state = StudyState(
        framework=request.framework,
        version=request.version,
        study_root=request.study_root,
        started_at=datetime.now()
    )
    
    # Async execution - checkpoints automatically
    background_tasks.add_task(
        run_study_pipeline,
        study_id=study_id,
        initial_state=initial_state,
        thread_id=thread_id
    )
    
    return StudyResponse(
        study_id=study_id,
        thread_id=thread_id,
        status="queued",
        estimated_completion=datetime.now() + timedelta(hours=2)
    )

async def run_study_pipeline(
    study_id: str,
    initial_state: StudyState,
    thread_id: str
):
    """Background task with full checkpointing"""
    async with AsyncPostgresSaver.from_conn_string(PG_URL) as checkpointer:
        graph = build_study_pipeline(checkpointer=checkpointer)
        config = {
            "configurable": {"thread_id": thread_id},
            "llm": app.state.llm
        }
        
        # Stream with events
        async for event in graph.astream(
            initial_state,
            config=config,
            stream_mode=["updates", "values"]
        ):
            # Log progress
            log_study_event(study_id, event)
            
        # Notify completion
        await notify_completed(study_id)
```

### 8.2 SSE Progress Streaming

```python
@router.get("/studies/{study_id}/events")
async def study_events(study_id: str):
    """Server-sent events for real-time progress"""
    async def event_generator():
        while True:
            # Poll checkpointer for this study's state
            state = await get_study_state(study_id)
            
            yield f"""data: {{
                "phase": "{state.current_phase}",
                "progress": {calculate_progress(state)},
                "chapters_complete": {sum(1 for c in state.chapters.values() if c.status == "complete")}
            }}\n\n"""
            
            if state.current_phase == "complete":
                break
                
            await asyncio.sleep(1)  # Poll interval
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
```

---

## 9. Directory Structure

```
COELHONexus/
├── apps/fastapi/
│   ├── graphs/
│   │   ├── study_pipeline/              # NEW MODULE
│   │   │   ├── __init__.py
│   │   │   ├── graph.py                 # Main StateGraph builder
│   │   │   ├── state.py                 # StudyState, ChapterState
│   │   │   ├── nodes/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── discovery.py         # Step 1: URL extraction
│   │   │   │   ├── fetcher.py           # Step 2: Content fetch
│   │   │   │   ├── planner.py           # Step 3: Chapter mapping
│   │   │   │   ├── synthesizer.py       # Step 4: Parallel synthesis
│   │   │   │   ├── critic.py            # Step 5: Validation
│   │   │   │   └── assembler.py         # Step 6: Final assembly
│   │   │   ├── prompts/
│   │   │   │   ├── discovery.txt
│   │   │   │   ├── planner.txt
│   │   │   │   ├── synthesizer_ch01.txt
│   │   │   │   ├── ... (ch02-ch08)
│   │   │   │   ├── critic.txt
│   │   │   │   └── assembler.txt
│   │   │   └── deep_agents.py           # DeepAgents config
│   │   ├── adaptive.py                  # EXISTING (YouTube RAG)
│   │   └── youtube.py                   # EXISTING (standard RAG)
│   ├── services/
│   │   ├── crawl4ai_client.py           # NEW: Crawl4AI wrapper
│   │   ├── retriever.py                 # EXISTING
│   │   └── ...
│   ├── schemas/
│   │   ├── study.py                     # NEW: Pydantic schemas
│   │   ├── state.py                     # EXISTING
│   │   └── ...
│   ├── routers/v1/
│   │   ├── studies.py                   # NEW: Study API routes
│   │   ├── youtube/                     # EXISTING
│   │   └── ...
│   └── app.py                           # MODIFIED: + studies router
└── docs/
    └── STUDY-GENERATOR-ARCHITECTURE-REFERENCE.md  # THIS FILE
```

---

## 10. Comparison: Study Generator vs. Adaptive RAG

| Aspect | Adaptive RAG (youtube.py) | Study Generator |
|--------|--------------------------|-----------------|
| **Trigger** | User query | Framework name |
| **Duration** | Seconds-minutes | Hours |
| **Output** | In-memory answer | Persistent file tree |
| **Granularity** | Document-level | Multi-level (raw/synth/chapter/summary) |
| **Sub-agents** | Same agent type | Different per phase |
| **Persistence** | Conversation history | Job checkpointing |
| **Pattern** | Query-driven RAG | Compiler pipeline |
| **LangGraph Feature** | Conditional edges | Send() parallel fan-out |
| **Architecture** | ReAct loop | 6-step linear pipeline |

**Key Insight:** These are different primitives that share FastAPI infrastructure but use different StateGraph patterns.

---

## 11. Implementation Sequence

### Phase 1: Foundation (Week 1)
1. Define `StudyState` and `ChapterState` schemas
2. Create module structure
3. Implement Discovery node with Crawl4AI
4. Implement Fetcher with asyncio.gather
5. Integration test: discovery → fetch

### Phase 2: Synthesis Engine (Week 2)
1. Implement Planner with structured output
2. Build Synthesizer fan-out with Send()
3. Configure multi-model routing (Kimi + GLM + Nemotron)
4. Test parallel chapter generation
5. Validate with DuckDB manual study

### Phase 3: Quality & Assembly (Week 3)
1. Implement Critic with RAGAS verification
2. Build retry loop for low-scoring chapters
3. Implement Assembler for summary/debt
4. Add FastAPI endpoints with SSE streaming
5. End-to-end testing

### Phase 4: Kubernetes & Scale (Week 4+)
1. K8s Job template for isolated execution
2. MinIO integration for raw HTML archive
3. Prometheus metrics
4. Grafana dashboard
5. LangSmith tracing

---

## 12. References

### DeepAgents
- GitHub: langchain-ai/deepagents (v0.5.3)
- Key Features: planning tools, filesystem ops, sub-agents, skills
- Pattern: Deep Research Agent with parallel Tavily subagents

### LangGraph
- Docs: pattern-library.parallel-fan-out
- GitHub: langchain-ai/langgraph
- Key: `Send()`, `Annotated[list, operator.add]`, `AsyncPostgresSaver`

### Crawl4AI
- Version: v0.8.x (April 2026)
- Features: `markdown.fit_markdown`, `LLMExtractionStrategy`, `arun_many()`
- Use: Documentation-to-Markdown conversion

### Model Selection (March 2026)
- Iternal AI LLM Selection Guide
- OpenRouter Programming Leaderboard
- Key: Multi-model routing for rate limit efficiency

---

*Last Updated: April 18, 2026*
*Status: Design Phase - Ready for Implementation*
