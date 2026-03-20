# OpenTelemetry & AI Agents Ecosystem Guide

**Author:** Rafael Coelho
**Date:** 2026-03-19
**Project:** COELHONexus

---

## Table of Contents

1. [OpenTelemetry - Learning & Implementation Guide](#1-opentelemetry---learning--implementation-guide)
   - [What is OpenTelemetry?](#what-is-opentelemetry)
   - [Key Concepts](#key-concepts-to-learn)
   - [Implementation for COELHONexus](#implementation-for-coelhonexus)
   - [Learning Path](#learning-path-recommendation)
2. [LangChain/LangGraph - Ecosystem Updates](#2-langchainlanggraph---what-changed-in-the-last-year)
   - [Major Changes Summary](#major-changes-summary)
   - [What You Need to Relearn](#what-you-need-to-relearn)
   - [DeepAgents](#deepagents---the-new-standard-for-complex-agents)
   - [Recommended Architecture](#recommended-architecture-for-youtube-content-search-microservices)
3. [Next Steps](#next-steps)

---

## 1. OpenTelemetry - Learning & Implementation Guide

### What is OpenTelemetry?

OpenTelemetry (OTel) is a **vendor-neutral observability framework** that standardizes the collection of telemetry data: **Traces**, **Metrics**, and **Logs** (the three pillars). It replaces the need for multiple proprietary instrumentation libraries.

### LGTM Stack Connection

```
┌─────────────────┐     OTLP      ┌────────────────┐     ┌──────────────────┐
│  COELHONexus    │ ───────────▶  │  Grafana Alloy │ ──▶ │  LGTM Stack      │
│  (FastAPI)      │               │  (Collector)   │     │  - Loki (Logs)   │
│                 │               │                │     │  - Tempo (Traces)│
│  OTel SDK       │               │                │     │  - Mimir (Metrics)│
└─────────────────┘               └────────────────┘     └──────────────────┘
```

### Key Concepts to Learn

| Concept | Description | Your Use Case |
|---------|-------------|---------------|
| **Traces** | End-to-end request flows across services | Track LLM agent calls, API latency |
| **Spans** | Individual operations within a trace | Each LangChain tool call, DB queries |
| **Metrics** | Numerical measurements (counters, gauges, histograms) | Request counts, LLM token usage |
| **Logs** | Structured event records | Debug info, agent reasoning steps |
| **Context Propagation** | Passing trace IDs across services | Correlate FastAPI → Redis → MinIO |
| **OTLP Protocol** | Standard protocol for exporting telemetry | How you send data to Alloy |

### Implementation for COELHONexus

#### Step 1: Add Dependencies

```toml
# pyproject.toml additions
dependencies = [
    # OpenTelemetry Core
    "opentelemetry-api>=1.29.0",
    "opentelemetry-sdk>=1.29.0",

    # OTLP Exporters (for Grafana Alloy)
    "opentelemetry-exporter-otlp>=1.29.0",

    # Auto-instrumentation
    "opentelemetry-instrumentation-fastapi>=0.50b0",
    "opentelemetry-instrumentation-httpx>=0.50b0",  # For LangChain HTTP calls
    "opentelemetry-instrumentation-redis>=0.50b0",

    # LangChain-specific (optional but recommended)
    "opentelemetry-instrumentation-langchain>=0.30.0",
]
```

#### Step 2: Initialize OpenTelemetry

Create `apps/fastapi/telemetry.py`:

```python
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import os

def setup_telemetry(app):
    """Initialize OpenTelemetry with OTLP export to Grafana Alloy."""

    # Resource identifies your service
    resource = Resource.create({
        SERVICE_NAME: "coelhonexus-fastapi",
        "service.namespace": "coelhonexus",
        "deployment.environment": os.getenv("ENV", "development"),
    })

    # Alloy endpoint (configure in your Helm values)
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy.coelhocloud:4317")

    # Traces
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(trace_provider)

    # Metrics
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True),
        export_interval_millis=30000,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    # Auto-instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)

    return trace.get_tracer(__name__)
```

#### Step 3: Instrument Your Agent Calls

```python
# routers/v1/agents.py
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@router.post("/search")
async def search_query(request: SearchQuery):
    with tracer.start_as_current_span("agent_search") as span:
        span.set_attribute("llm.provider", request.provider)
        span.set_attribute("query.length", len(request.query))

        # Your LangChain agent call
        result = await agent.invoke(request.query)

        span.set_attribute("response.tokens", result.usage.total_tokens)
        return result
```

#### Step 4: Configure Grafana Alloy

In COELHO Cloud, configure Alloy to receive OTLP and forward to LGTM:

```river
// alloy config
otelcol.receiver.otlp "default" {
    grpc { endpoint = "0.0.0.0:4317" }
    http { endpoint = "0.0.0.0:4318" }
    output {
        traces  = [otelcol.exporter.otlp.tempo.input]
        metrics = [otelcol.exporter.prometheus.mimir.input]
        logs    = [otelcol.exporter.loki.default.input]
    }
}
```

#### Step 5: Update Helm Values

Add to `k3d/helm/values.yaml`:

```yaml
env:
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://alloy.coelhocloud:4317"
  OTEL_SERVICE_NAME: "coelhonexus-fastapi"
  OTEL_RESOURCE_ATTRIBUTES: "service.namespace=coelhonexus,deployment.environment=development"
```

### Learning Path Recommendation

| Day | Focus Area | Activities |
|-----|------------|------------|
| **1-2** | Concepts | Understand traces, metrics, logs, and OTLP protocol |
| **3** | Basic Tracing | Implement basic tracing in FastAPI app |
| **4** | Custom Spans | Add custom spans for LangChain agent calls |
| **5** | Metrics | Configure metrics and create Grafana dashboards |
| **6-7** | Logging | Add structured logging with trace correlation |

### Useful Resources

- [OpenTelemetry Python Docs](https://opentelemetry.io/docs/instrumentation/python/)
- [Grafana Alloy OTLP Receiver](https://grafana.com/docs/alloy/latest/reference/components/otelcol.receiver.otlp/)
- [OpenTelemetry FastAPI Instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html)

---

## 2. LangChain/LangGraph - What Changed in the Last Year

### Major Changes Summary

| Release | Date | Key Changes |
|---------|------|-------------|
| **LangChain 1.0** | Oct 2025 | Stability release, `.content_blocks`, Python 3.10+ required |
| **LangChain 1.1** | Feb 2026 | Middleware system (retry, moderation, summarization) |
| **LangGraph 1.0** | Oct 2025 | Production-ready, type-safe streaming, node caching |
| **LangGraph 1.1** | Jan 2026 | Multi-agent libraries (Supervisor, Swarm patterns) |
| **DeepAgents** | Mar 2026 | Agent harness with planning, memory, subagent spawning |

### What You Need to Relearn

#### 1. Type-Safe Streaming (LangGraph 1.0+)

```python
# OLD (your COELHO Agents era)
for chunk in agent.stream({"input": query}):
    print(chunk)

# NEW (v2 streaming)
for part in agent.stream({"input": query}, version="v2"):
    # Unified StreamPart with type, ns, data
    if part.type == "values":
        print(part.data)
```

#### 2. Content Blocks (LangChain 1.0+)

```python
# NEW: Consistent content across all LLM providers
response = llm.invoke("Hello")
for block in response.content_blocks:
    if block.type == "text":
        print(block.text)
    elif block.type == "tool_use":
        print(block.tool_calls)
```

#### 3. Middleware System (LangChain 1.1)

```python
from langchain.middleware import RetryMiddleware, ModerationMiddleware

# Automatic retries with exponential backoff
chain = RetryMiddleware(max_retries=3) | llm | parser

# Content moderation
chain = ModerationMiddleware() | llm | parser
```

#### 4. Node/Task Level Caching (LangGraph 1.0+)

```python
from langgraph.graph import StateGraph
from langgraph.cache import NodeCache

graph = StateGraph(State)
graph.add_node("expensive_llm_call", llm_node, cache=NodeCache(ttl=3600))
```

#### 5. Multi-Agent Systems (LangGraph 1.1)

```python
from langgraph.prebuilt import create_supervisor

# Hierarchical multi-agent system
supervisor = create_supervisor(
    agents=[research_agent, writing_agent, review_agent],
    model=llm,
)
```

### DeepAgents - The New Standard for Complex Agents

DeepAgents is built on LangGraph and provides:

- **Built-in planning**: `write_todos` tool for task decomposition
- **Filesystem tools**: Manage large context windows
- **Subagent spawning**: Isolated context for parallel tasks
- **Persistent memory**: Via LangGraph Memory Store

```python
# DeepAgents example
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="claude-sonnet-4-6",
    tools=[your_youtube_search_tool, your_neo4j_tool],
    memory_store=memory,
)

# Agent automatically plans, spawns subagents, manages memory
result = await agent.run("Search YouTube for ML tutorials and build a knowledge graph")
```

### Framework Comparison

| Framework | Best For | Strengths |
|-----------|----------|-----------|
| **LangChain/LangGraph** | Production-grade complex workflows | Largest ecosystem, best observability (LangSmith), type-safe streaming |
| **CrewAI** | Role-based multi-agent teams | Fastest prototyping, intuitive YAML configuration |
| **AutoGen** | Conversational multi-agent systems | Group decision-making, no-code Studio, Microsoft ecosystem |
| **DeepAgents** | Autonomous long-running agents | Built-in planning, memory, subagent spawning |

### Recommended Architecture for YouTube Content Search (Microservices)

Based on COELHO RealTime reference:

```
┌─────────────────────────────────────────────────────────────────┐
│                      COELHONexus                                │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐   │
│  │ API Gateway │──▶│ Agent       │──▶│ YouTube Search      │   │
│  │ (FastAPI)   │   │ Orchestrator│   │ Service             │   │
│  └─────────────┘   │ (LangGraph) │   │ (YouTube API +      │   │
│        │           └─────────────┘   │  Transcript Extract)│   │
│        │                  │          └─────────────────────┘   │
│        ▼                  ▼                     │               │
│  ┌─────────────┐   ┌─────────────┐              ▼               │
│  │ OTel Export │   │ Knowledge   │   ┌─────────────────────┐   │
│  │ → Alloy     │   │ Graph       │   │ Vector Store        │   │
│  └─────────────┘   │ (Neo4j/     │   │ (Embeddings +       │   │
│                    │  GraphRAG)  │   │  Semantic Search)   │   │
│                    └─────────────┘   └─────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Service Breakdown

| Service | Responsibility | Technologies |
|---------|----------------|--------------|
| **API Gateway** | Request routing, auth, rate limiting | FastAPI |
| **Agent Orchestrator** | Multi-agent coordination, planning | LangGraph + DeepAgents |
| **YouTube Search Service** | Video search, transcript extraction | YouTube API, whisper |
| **Knowledge Graph** | Entity relationships, GraphRAG | Neo4j, LangChain |
| **Vector Store** | Semantic search, embeddings | Qdrant/Milvus, OpenAI embeddings |

---

## Next Steps

### Immediate Actions

1. **OpenTelemetry Setup**
   - [ ] Add OTel dependencies to `pyproject.toml`
   - [ ] Create `telemetry.py` module
   - [ ] Configure Grafana Alloy receiver
   - [ ] Verify traces in Tempo

2. **LangChain/LangGraph Update**
   - [ ] Upgrade to `langchain>=1.1.0` and `langgraph>=1.1.0`
   - [ ] Review breaking changes in migration guides
   - [ ] Test existing agent code with new APIs

3. **DeepAgents Exploration**
   - [ ] Install: `pip install deepagents`
   - [ ] Review documentation and examples
   - [ ] Prototype YouTube search agent

### Week 1 Goals

- Complete OpenTelemetry integration with basic tracing
- Update LangChain dependencies and fix breaking changes
- Create first Grafana dashboard for agent metrics

### Week 2 Goals

- Implement custom spans for all LLM calls
- Add structured logging with trace correlation
- Design microservices architecture for YouTube Content Search

---

## References

### OpenTelemetry

- [OpenTelemetry Python Documentation](https://opentelemetry.io/docs/instrumentation/python/)
- [Grafana Alloy Documentation](https://grafana.com/docs/alloy/latest/)
- [OTLP Specification](https://opentelemetry.io/docs/specs/otlp/)

### LangChain/LangGraph

- [LangChain Changelog](https://changelog.langchain.com/)
- [LangGraph 1.0 Announcement](https://changelog.langchain.com/announcements/langgraph-1-0-is-now-generally-available)
- [DeepAgents Documentation](https://docs.langchain.com/oss/python/deepagents/overview)
- [LangChain 1.0 Migration Guide](https://python.langchain.com/docs/versions/v0_3/)

### AI Agents Ecosystem

- [LangChain vs LangGraph Comparison](https://kanerika.com/blogs/langchain-vs-langgraph/)
- [Multi-Agent Systems with LangGraph](https://langchain-ai.github.io/langgraph/concepts/multi_agent/)
- [DeepAgents Blog Post](https://blog.langchain.com/deep-agents/)
