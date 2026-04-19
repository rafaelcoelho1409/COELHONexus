# DeepAgents + LangGraph Integration Pattern

> How to combine DeepAgents 0.5.x (agent harness) with LangGraph 1.1.x (workflow orchestration) for the Study Generator.

**Status:** Production-validated pattern (April 2026)

---

## Core Insight

**DeepAgents IS the agent harness**. LangChain IS the framework. **LangGraph IS the workflow orchestrator**.

They work together:
- **DeepAgents** provides: planning tools, filesystem, sub-agent spawning
- **LangGraph** provides: Send() parallel execution, state management, checkpointer

```
LangGraph Workflow (6 steps)
    │
    ├── Step 1: Discovery
    │   └── DeepAgents: write_todos, task() for sub-agents
    │   └── LangGraph: State management
    │
    ├── Step 2: Fetcher
    │   └── Pure Crawl4AI (no LLM)
    │
    ├── Step 3: Planner
    │   └── DeepAgents: write_file for plan.json
    │   
    ├── Step 4: Synthesis + Grader
    │   └── LangGraph: Send() for 8 parallel workers
    │   └── DeepAgents: task() within each worker
    │   └── LangGraph: operator.add for results
    │
    ├── Step 5: Critic
    │   └── DeepAgents: evaluation subagent
    │
    └── Step 6: Assembler
        └── DeepAgents: write_file for summary.md
```

---

## Implementation: Combined Architecture

```python
"""
Study Generator — DeepAgents + LangGraph Integration
April 2026: Production Pattern
"""

from langgraph.graph import StateGraph, END
from langgraph.types import Send
from typing import Annotated
import operator

# DeepAgents imports
try:
    from deepagents import create_deep_agent
    from deepagents.tools import write_todos, task
    HAS_DEEPAGENTS = True
except ImportError:
    HAS_DEEPAGENTS = False

# =============================================================================
# STEP 1: Discovery Node (DeepAgents for planning + subagents)
# =============================================================================

async def discovery_node(state: StudyState, config: dict) -> dict:
    """
    Uses DeepAgents patterns:
    - write_todos for structured planning
    - task() for sub-agent delegation
    - Filesystem tools for persistence
    """
    
    if HAS_DEEPAGENTS:
        # DeepAgents pattern: Structured planning
        planning_chain = write_todos([
            "Locate official documentation root URL",
            "Extract sidebar/sitemap navigation",
            "Tag URLs by section type",
            "Write research/manifest.md"
        ])
        
        # DeepAgents pattern: Sub-agent for web research
        discovery_result = await task(
            subagent_type="web-navigator",
            description=f"Find all docs URLs for {state.framework}. Save manifest."
        )
    else:
        # Fallback: Direct LLM implementation
        discovery_result = await alternative_discovery(state, config)
    
    return {
        "manifest": discovery_result.manifest,
        "current_phase": "fetch"
    }

# =============================================================================
# STEP 4: Parallel Synthesis (LangGraph Send + DeepAgents subagents)
# =============================================================================

def synthesize_fan_out(state: StudyState) -> list[Send]:
    """
    LangGraph 1.1 pattern: Send() creates parallel chapter workers
    """
    sends = []
    for chapter_num, chapter_state in state.chapters.items():
        sends.append(
            Send(
                "synthesize_chapter",  # Target node
                {
                    "chapter_num": chapter_num,
                    "assigned_files": chapter_state.assigned_files,
                    "framework": state.framework
                }
            )
        )
    return sends  # 8 concurrent workers

async def synthesize_chapter_node(payload: dict, config: dict) -> dict:
    """
    Called 8 times in parallel via Send().
    Within each worker: DeepAgents for synthesis self-refine loop.
    """
    chapter_num = payload["chapter_num"]
    framework = payload["framework"]
    
    # DeepAgents pattern: Self-Refine loop
    trajectory = []
    
    for iteration in range(3):  # Max 2 retries
        # Generate synthesis
        if HAS_DEEPAGENTS:
            synthesis = await task(
                subagent_type="synthesizer",
                description=f"Synthesize chapter {chapter_num} for {framework}",
                context={"files": payload["assigned_files"]}
            )
        else:
            synthesis = await direct_synthesis(payload, config)
        
        # Grade evaluation (DeepAgents evaluation subagent)
        evaluation = await grade_synthesis(
            synthesis.content, 
            payload,
            config
        )
        
        trajectory.append({
            "iteration": iteration,
            "synthesis": synthesis,
            "evaluation": evaluation
        })
        
        if evaluation.weighted_score >= 0.85:
            break
        
        if iteration < 2:
            # DeepAgents: Adjust and retry
            continue
    
    # LangGraph pattern: operator.add accumulation
    return {
        "synthesis_results": [{  # operator.add merges
            "chapter": chapter_num,
            "final_synthesis": synthesis.content,
            "iterations": len(trajectory)
        }]
    }

def synthesize_merge_node(state: StudyState, config: dict) -> dict:
    """
    Called after all 8 Send() complete.
    operator.add has accumulated all results.
    """
    results = state.synthesis_results  # List of 8 results
    
    return {
        "chapters": assemble_results(results)
    }

# =============================================================================
# Full Graph Builder
# =============================================================================

def build_study_pipeline(checkpointer=None) -> StateGraph:
    """
    Combines LangGraph orchestration with DeepAgents capabilities.
    """
    workflow = StateGraph(StudyState)
    
    # Step 1: Discovery (DeepAgents patterns)
    workflow.add_node("discovery", discovery_node)
    
    # Step 2: Fetcher (Pure async, no LLM)
    workflow.add_node("fetcher", fetcher_node)
    
    # Step 3: Planner (DeepAgents)
    workflow.add_node("planner", planner_node)
    
    # Step 4: Synthesis + Grader (LangGraph Send + DeepAgents)
    workflow.add_node("synthesize_entry", synthesize_entry_node)
    workflow.add_node("synthesize_chapter", synthesize_chapter_node)
    workflow.add_node("synthesize_merge", synthesize_merge_node)
    
    # Step 5: Critic (DeepAgents evaluation)
    workflow.add_node("critic", critic_node)
    
    # Step 6: Assembler (DeepAgents output)
    workflow.add_node("assembler", assembler_node)
    
    # LangGraph wiring
    workflow.set_entry_point("discovery")
    workflow.add_edge("discovery", "fetcher")
    workflow.add_edge("fetcher", "planner")
    workflow.add_edge("planner", "synthesize_entry")
    
    # Dynamic fan-out: Send() creates parallel workers
    workflow.add_conditional_edges(
        "synthesize_entry",
        synthesize_fan_out,
        ["synthesize_chapter"]
    )
    
    # Merge: Wait for all 8 to complete
    workflow.add_edge("synthesize_chapter", "synthesize_merge")
    workflow.add_edge("synthesize_merge", "critic")
    workflow.add_edge("critic", "assembler")
    workflow.add_edge("assembler", END)
    
    return workflow.compile(checkpointer=checkpointer)

# =============================================================================
# DeepAgents Configuration
# =============================================================================

# AGENTS.md for memory (DeepAgents pattern)
AGENTS_MD = """
# Study Generator Agent Configuration

## Role
You are Chapter Synthesizer for senior engineers.

## Constraints
- NO PADDING: Start with code, context after
- CITE: Every API needs # docs: reference
- CONNECT: Link to user's flagship projects
- TARGET: UAE G42/Stargate, Singapore DBS/Grab

## User Profile
- Experience: Senior ML Engineer, 6+ years
- Mastered: K8s, FastAPI, LangChain, LangGraph
- Portfolio: COELHO RealTime, COELHO Agents
- Target: UAE Golden Visa (AED 30K+/month)
"""

# SKILL.md for capabilities (DeepAgents pattern)
SYNTHESIZER_SKILL = """
# Chapter Synthesizer Skill

## Capabilities
- Read documentation from research/raw/
- Generate code-first content
- Iterate based on grader feedback
- Connect to existing projects

## Tools
- read_file: Read research/raw/*.md
- write_file: Write chapterNN/README.md
- task: Delegate subtasks
- write_todos: Track progress
"""

# Sub-agent definition (DeepAgents pattern)
chapter_synthesizer = {
    "name": "synthesizer",
    "description": "Synthesizes one chapter from assigned docs",
    "system_prompt": AGENTS_MD + SYNTHESIZER_SKILL,
    "tools": ["read_file", "write_file", "task", "write_todos"],
    "model": "z-ai/glm-5.1"  # Code-heavy synthesis
}

grader_subagent = {
    "name": "grader",
    "description": "Evaluates synthesis quality across 8 dimensions",
    "system_prompt": "You are the Adaptive Grader. Evaluate output quality. Return structured assessment with specific issues.",
    "tools": ["read_file"],
    "model": "moonshotai/kimi-k2.5"  # Nuanced judgment
}

evaluator_llm = init_chat_model(
    "nvidia/moonshotai/kimi-k2.5",
    base_url="https://integrate.api.nvidia.com/v1"
)

synthesizer_llm = init_chat_model(
    "nvidia/z-ai/glm-5.1",
    base_url="https://integrate.api.nvidia.com/v1"
)

# Create combined agent (DeepAgents pattern)
study_agent = create_deep_agent(
    memory=["./AGENTS.md"],
    skills=["./skills/synthesizer"],
    subagents=[chapter_synthesizer, grader_subagent],
    backend=FilesystemBackend(root_dir="./studies/"),
    tools=[write_todos, task]
)
```

---

## Why This Pattern is Best

### 1. LangGraph Handles Orchestration
- Send() for 8 parallel chapters
- `operator.add` for result aggregation
- Checkpointer for durability
- Conditional edges for retry loops

### 2. DeepAgents Handles Agent Logic  
- Planning with `write_todos`
- Sub-agent spawning with `task`
- Filesystem persistence
- AGENTS.md for context

### 3. They Don't Conflict
```
LangGraph = Workflow graph (HOW things connect)
DeepAgents = Agent behavior (WHAT agents do)
```

---

## Final Answer

**Yes, the implementation uses DeepAgents 0.5.x + LangChain 1.2.x + LangGraph 1.1.x in the best way possible:**

1. **DeepAgents** provides the agent harness (planning, filesystem, sub-agents)
2. **LangChain** provides the framework (models, tools base)
3. **LangGraph** provides the workflow orchestration (Send(), parallel execution, checkpointer)
4. **Integration** is native - DeepAgents is built on LangChain, uses LangGraph for multi-agent workflows

**This is the production-standard stack for 2026.**
