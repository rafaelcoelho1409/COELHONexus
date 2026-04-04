# RAG 2026: State-of-the-Art Architecture Research

> Deep research compilation for building the best possible RAG system.
> All code uses **DeepAgents**, **LangChain**, and **LangGraph** - ready to apply to your project.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Dependencies](#dependencies)
3. [Agentic RAG with LangGraph](#agentic-rag-with-langgraph)
4. [Chunking with LangChain](#chunking-with-langchain)
5. [Embedding Models](#embedding-models)
6. [Reranking](#reranking)
7. [Hybrid Retrieval: Qdrant + Neo4j](#hybrid-retrieval-qdrant--neo4j)
8. [Multi-Agent with LangGraph](#multi-agent-with-langgraph)
9. [DeepAgents Integration](#deepagents-integration)
10. [Caching Strategies](#caching-strategies)
11. [Evaluation with RAGAS](#evaluation-with-ragas)
12. [Complete Example: YouTube RAG Agent](#complete-example-youtube-rag-agent)
13. [Production Checklist](#production-checklist)
14. [Implementation Roadmap](#implementation-roadmap)
15. [Sources](#sources)

---

## Executive Summary

### 2026 RAG Landscape

| Evolution | Traditional RAG | Agentic RAG (2026) |
|-----------|-----------------|-------------------|
| Pipeline | Fixed sequence | Agent-controlled loops |
| Retrieval | Single-pass | Self-correcting, multi-hop |
| Architecture | Monolithic | Multi-agent, supervisor/router |
| Accuracy | ~70% | 78-94.5% (HotpotQA) |
| Latency | 300-500ms | <200ms (with caching) |

### Key Insight

> "By early 2026, enterprise RAG teams have learned that **retrieval quality matters far more than generation quality**, and the fix is closed-loop retrieval: retrieve, evaluate, and try again until the evidence supports a good answer."

---

## Dependencies

### Install Required Packages

```bash
# Core
pip install langchain langchain-core langgraph deepagents

# Vector Store
pip install langchain-qdrant qdrant-client

# Graph Database
pip install langchain-neo4j neo4j

# Embeddings
pip install langchain-openai langchain-cohere sentence-transformers

# Reranking
pip install flashrank cohere

# Evaluation
pip install ragas datasets

# Caching
pip install redis gptcache
```

### Project Structure

```
apps/fastapi/
├── services/
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── state.py          # LangGraph state definitions
│   │   ├── nodes.py          # Graph nodes (retrieve, grade, generate)
│   │   ├── graph.py          # LangGraph workflow
│   │   ├── retrievers.py     # Qdrant + Neo4j retrievers
│   │   └── tools.py          # DeepAgents tools
│   ├── embeddings.py         # Embedding service
│   ├── chunker.py            # Text chunking
│   └── reranker.py           # Reranking service
├── agents/
│   └── youtube_rag.py        # DeepAgents YouTube RAG
└── routers/v1/youtube/
    └── rag.py                # RAG endpoints
```

---

## Agentic RAG with LangGraph

### State Definition

```python
# services/rag/state.py
from typing import TypedDict, List, Literal, Annotated
from langchain_core.documents import Document
from langgraph.graph.message import add_messages

class RAGState(TypedDict):
    """State for the RAG workflow."""
    # Input
    question: str

    # Retrieval
    documents: List[Document]

    # Generation
    generation: str

    # Control flow
    retry_count: int

    # Messages (for DeepAgents compatibility)
    messages: Annotated[list, add_messages]


class GradeDocuments(TypedDict):
    """Binary score for document relevance."""
    score: Literal["relevant", "not_relevant"]


class RouteQuery(TypedDict):
    """Route query to appropriate retriever."""
    route: Literal["vector_search", "graph_search", "hybrid"]
```

### Graph Nodes

```python
# services/rag/nodes.py
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .state import RAGState, GradeDocuments

# Initialize LLM (use your NVIDIA NIM or other provider)
llm = ChatOpenAI(
    model="meta/llama-3.3-70b-instruct",
    base_url="https://integrate.api.nvidia.com/v1",
    temperature=0,
)


# --- RETRIEVE NODE ---
async def retrieve(state: RAGState) -> dict:
    """Retrieve documents from Qdrant + Neo4j."""
    from .retrievers import hybrid_retriever

    question = state["question"]
    documents = await hybrid_retriever.ainvoke(question)

    return {"documents": documents}


# --- GRADE DOCUMENTS NODE ---
async def grade_documents(state: RAGState) -> dict:
    """Grade retrieved documents for relevance."""

    # Structured output for grading
    grader_llm = llm.with_structured_output(GradeDocuments)

    grade_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a grader assessing relevance of a retrieved document to a user question.
If the document contains keyword(s) or semantic meaning related to the question, grade it as relevant.
Give a binary score 'relevant' or 'not_relevant'."""),
        ("human", "Document: {document}\n\nQuestion: {question}"),
    ])

    grader = grade_prompt | grader_llm

    question = state["question"]
    documents = state["documents"]

    # Grade each document
    relevant_docs = []
    for doc in documents:
        result = await grader.ainvoke({
            "document": doc.page_content,
            "question": question,
        })
        if result["score"] == "relevant":
            relevant_docs.append(doc)

    return {"documents": relevant_docs}


# --- GENERATE NODE ---
async def generate(state: RAGState) -> dict:
    """Generate answer using retrieved documents."""

    generate_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are an assistant answering questions about YouTube video transcripts.
Use the following context to answer the question. Include video timestamps when available.
If you don't know the answer, say so. Don't make up information."""),
        ("human", """Context:
{context}

Question: {question}

Answer:"""),
    ])

    chain = generate_prompt | llm | StrOutputParser()

    # Format documents as context
    context = "\n\n".join([
        f"[Video: {doc.metadata.get('video_title', 'Unknown')}]\n{doc.page_content}"
        for doc in state["documents"]
    ])

    generation = await chain.ainvoke({
        "context": context,
        "question": state["question"],
    })

    return {"generation": generation}


# --- REWRITE QUERY NODE ---
async def rewrite_query(state: RAGState) -> dict:
    """Rewrite query for better retrieval."""

    rewrite_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a query rewriter. Improve the query for better semantic search.
Add synonyms, expand abbreviations, and clarify intent.
Return only the rewritten query, nothing else."""),
        ("human", "Original query: {question}"),
    ])

    chain = rewrite_prompt | llm | StrOutputParser()

    new_question = await chain.ainvoke({"question": state["question"]})

    return {
        "question": new_question,
        "retry_count": state.get("retry_count", 0) + 1,
    }


# --- HALLUCINATION CHECK NODE ---
async def check_hallucination(state: RAGState) -> dict:
    """Check if generation is grounded in documents."""

    class HallucinationCheck(BaseModel):
        grounded: bool = Field(description="Is the answer grounded in the documents?")
        addresses_question: bool = Field(description="Does the answer address the question?")

    checker_llm = llm.with_structured_output(HallucinationCheck)

    check_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a fact-checker. Determine:
1. Is the answer grounded in the provided documents?
2. Does the answer address the user's question?"""),
        ("human", """Documents:
{documents}

Question: {question}

Answer: {generation}"""),
    ])

    chain = check_prompt | checker_llm

    result = await chain.ainvoke({
        "documents": "\n".join([d.page_content for d in state["documents"]]),
        "question": state["question"],
        "generation": state["generation"],
    })

    return {"grounded": result.grounded and result.addresses_question}


# --- ROUTING FUNCTIONS ---
def should_rewrite(state: RAGState) -> str:
    """Decide if we should rewrite the query."""
    if not state["documents"]:
        if state.get("retry_count", 0) < 3:
            return "rewrite"
        return "fail"
    return "generate"


def check_grounding(state: RAGState) -> str:
    """Decide next step after hallucination check."""
    if state.get("grounded", False):
        return "end"
    if state.get("retry_count", 0) < 3:
        return "rewrite"
    return "end"  # Return anyway after max retries
```

### Build the Graph

```python
# services/rag/graph.py
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from .state import RAGState
from .nodes import (
    retrieve,
    grade_documents,
    generate,
    rewrite_query,
    check_hallucination,
    should_rewrite,
    check_grounding,
)


def create_rag_graph(checkpointer=None):
    """Create the agentic RAG workflow."""

    # Build graph
    workflow = StateGraph(RAGState)

    # Add nodes
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("grade", grade_documents)
    workflow.add_node("generate", generate)
    workflow.add_node("rewrite", rewrite_query)
    workflow.add_node("check_hallucination", check_hallucination)

    # Define edges
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "grade")

    # Conditional: grade → generate or rewrite
    workflow.add_conditional_edges(
        "grade",
        should_rewrite,
        {
            "generate": "generate",
            "rewrite": "rewrite",
            "fail": END,
        }
    )

    # Rewrite loops back to retrieve
    workflow.add_edge("rewrite", "retrieve")

    # Generate → check hallucination
    workflow.add_edge("generate", "check_hallucination")

    # Conditional: check → end or rewrite
    workflow.add_conditional_edges(
        "check_hallucination",
        check_grounding,
        {
            "end": END,
            "rewrite": "rewrite",
        }
    )

    # Compile with optional checkpointer
    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


# Usage with Redis checkpointer (from your app.py)
async def get_rag_agent(redis_url: str):
    async with AsyncRedisSaver.from_conn_string(redis_url) as checkpointer:
        await checkpointer.setup()
        return create_rag_graph(checkpointer)
```

### Using the RAG Agent

```python
# Example usage in FastAPI endpoint
from fastapi import APIRouter, Request
from services.rag.graph import create_rag_graph

router = APIRouter()

@router.post("/rag/query")
async def query_rag(question: str, request: Request):
    # Create graph with checkpointer from app state
    graph = create_rag_graph(request.app.state.checkpointer)

    # Run the agent
    result = await graph.ainvoke(
        {
            "question": question,
            "documents": [],
            "generation": "",
            "retry_count": 0,
            "messages": [],
        },
        config={"configurable": {"thread_id": "user-123"}},
    )

    return {
        "question": question,
        "answer": result["generation"],
        "sources": [
            {
                "video_id": doc.metadata.get("video_id"),
                "title": doc.metadata.get("video_title"),
            }
            for doc in result["documents"]
        ],
    }
```

---

## Chunking with LangChain

### Recursive Character Splitter (Recommended)

```python
# services/chunker.py
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


def create_chunker(
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> RecursiveCharacterTextSplitter:
    """Create a recursive text splitter."""
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def chunk_transcript(
    transcript: str,
    video_id: str,
    video_title: str,
    chunker: RecursiveCharacterTextSplitter = None,
) -> list[Document]:
    """Chunk a video transcript into documents."""
    if chunker is None:
        chunker = create_chunker()

    # Split text
    chunks = chunker.split_text(transcript)

    # Create documents with metadata
    documents = []
    for i, chunk in enumerate(chunks):
        doc = Document(
            page_content=chunk,
            metadata={
                "video_id": video_id,
                "video_title": video_title,
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
        )
        documents.append(doc)

    return documents
```

### Contextual Chunking (Anthropic Style)

```python
# services/chunker.py (continued)
from langchain_openai import ChatOpenAI

async def add_chunk_context(
    documents: list[Document],
    full_document: str,
    llm: ChatOpenAI = None,
) -> list[Document]:
    """Add context to each chunk for better retrieval."""
    if llm is None:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    contextualized = []

    for doc in documents:
        # Generate context for chunk
        context = await llm.ainvoke(
            f"""Given this full document:
{full_document[:2000]}...

And this chunk:
{doc.page_content}

Write a brief context (1-2 sentences) that situates this chunk within the document.
Return only the context, nothing else."""
        )

        # Prepend context to chunk
        contextualized_content = f"[CONTEXT: {context.content}]\n\n{doc.page_content}"

        contextualized.append(Document(
            page_content=contextualized_content,
            metadata=doc.metadata,
        ))

    return contextualized
```

---

## Embedding Models

### Using Different Embedding Providers

```python
# services/embeddings.py
from langchain_openai import OpenAIEmbeddings
from langchain_cohere import CohereEmbeddings
from langchain_community.embeddings import HuggingFaceBgeEmbeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings


def get_embeddings(provider: str = "openai"):
    """Get embedding model by provider."""

    match provider:
        case "openai":
            return OpenAIEmbeddings(
                model="text-embedding-3-large",
                dimensions=1024,  # MRL: can reduce for speed
            )

        case "cohere":
            return CohereEmbeddings(
                model="embed-english-v3.0",
            )

        case "bge":
            # Best open-source, runs locally
            return HuggingFaceBgeEmbeddings(
                model_name="BAAI/bge-m3",
                model_kwargs={"device": "cuda"},
                encode_kwargs={"normalize_embeddings": True},
            )

        case "gemini":
            return GoogleGenerativeAIEmbeddings(
                model="models/text-embedding-004",
            )

        case _:
            raise ValueError(f"Unknown provider: {provider}")
```

---

## Reranking

### Using FlashRank (Fast, Open-Source)

```python
# services/reranker.py
from flashrank import Ranker, RerankRequest
from langchain_core.documents import Document


class FlashReranker:
    """Fast reranking with FlashRank."""

    def __init__(self, model_name: str = "ms-marco-MiniLM-L-12-v2"):
        self.ranker = Ranker(model_name=model_name)

    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: int = 5,
    ) -> list[Document]:
        """Rerank documents by relevance to query."""

        # Prepare passages
        passages = [
            {"id": i, "text": doc.page_content, "meta": doc.metadata}
            for i, doc in enumerate(documents)
        ]

        # Rerank
        request = RerankRequest(query=query, passages=passages)
        results = self.ranker.rerank(request)

        # Return top_k reranked documents
        reranked = []
        for result in results[:top_k]:
            doc = Document(
                page_content=result["text"],
                metadata={**result["meta"], "rerank_score": result["score"]},
            )
            reranked.append(doc)

        return reranked


# Alternative: Cohere Rerank (API)
from cohere import Client

class CohereReranker:
    def __init__(self, api_key: str):
        self.client = Client(api_key)

    def rerank(
        self,
        query: str,
        documents: list[Document],
        top_k: int = 5,
    ) -> list[Document]:
        results = self.client.rerank(
            model="rerank-english-v3.0",
            query=query,
            documents=[doc.page_content for doc in documents],
            top_n=top_k,
        )

        reranked = []
        for result in results.results:
            original_doc = documents[result.index]
            reranked.append(Document(
                page_content=original_doc.page_content,
                metadata={**original_doc.metadata, "rerank_score": result.relevance_score},
            ))

        return reranked
```

---

## Hybrid Retrieval: Qdrant + Neo4j

### Qdrant Vector Store

```python
# services/rag/retrievers.py
from langchain_qdrant import QdrantVectorStore, RetrievalMode
from langchain_qdrant import FastEmbedSparse, SparseEmbeddings
from qdrant_client import QdrantClient

from services.embeddings import get_embeddings


def create_qdrant_retriever(
    url: str = "http://localhost:6333",
    collection_name: str = "youtube_transcripts",
    retrieval_mode: str = "hybrid",  # dense, sparse, or hybrid
):
    """Create Qdrant retriever with hybrid search support."""

    client = QdrantClient(url=url)
    embeddings = get_embeddings("openai")

    # For hybrid mode, also need sparse embeddings
    sparse_embeddings = None
    if retrieval_mode in ("sparse", "hybrid"):
        sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")

    # Determine retrieval mode
    mode_map = {
        "dense": RetrievalMode.DENSE,
        "sparse": RetrievalMode.SPARSE,
        "hybrid": RetrievalMode.HYBRID,
    }

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings,
        sparse_embedding=sparse_embeddings,
        retrieval_mode=mode_map[retrieval_mode],
    )

    # Create retriever with MMR for diversity
    return vector_store.as_retriever(
        search_type="mmr",
        search_kwargs={
            "k": 20,
            "fetch_k": 50,
            "lambda_mult": 0.7,  # Balance relevance vs diversity
        }
    )
```

### Neo4j Graph Store

```python
# services/rag/retrievers.py (continued)
from langchain_neo4j import Neo4jGraph, Neo4jVector
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_openai import ChatOpenAI


def create_neo4j_graph(
    url: str,
    username: str,
    password: str,
) -> Neo4jGraph:
    """Create Neo4j graph connection."""
    return Neo4jGraph(
        url=url,
        username=username,
        password=password,
    )


async def extract_and_store_graph(
    documents: list,
    graph: Neo4jGraph,
    llm: ChatOpenAI = None,
):
    """Extract entities and relationships, store in Neo4j."""
    if llm is None:
        llm = ChatOpenAI(model="gpt-4o", temperature=0)

    # LLMGraphTransformer extracts (subject, predicate, object) triples
    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=["Video", "Topic", "Channel", "Person", "Concept"],
        allowed_relationships=[
            "MENTIONS", "BELONGS_TO", "RELATED_TO", "DISCUSSES", "FEATURES"
        ],
    )

    # Convert documents to graph documents
    graph_documents = await transformer.aconvert_to_graph_documents(documents)

    # Store in Neo4j
    graph.add_graph_documents(
        graph_documents,
        baseEntityLabel=True,
        include_source=True,
    )


def create_neo4j_retriever(
    graph: Neo4jGraph,
    embeddings,
    index_name: str = "youtube_vector",
):
    """Create Neo4j vector retriever."""
    return Neo4jVector.from_existing_graph(
        embedding=embeddings,
        graph=graph,
        index_name=index_name,
        node_label="Document",
        text_node_properties=["text"],
        embedding_node_property="embedding",
    ).as_retriever(search_kwargs={"k": 10})


def graph_query_retriever(graph: Neo4jGraph, entities: list[str]) -> list:
    """Query Neo4j for related content."""
    query = """
    MATCH (v:Video)-[:MENTIONS]->(t:Topic)
    WHERE t.name IN $entities
    WITH v, collect(t.name) as topics, count(t) as relevance
    ORDER BY relevance DESC
    LIMIT 20
    MATCH (v)-[:HAS_CHUNK]->(c:Chunk)
    RETURN c.text as content, v.title as video_title, v.id as video_id, topics
    """

    results = graph.query(query, {"entities": entities})
    return results
```

### Hybrid Retriever with Fusion

```python
# services/rag/retrievers.py (continued)
from langchain.retrievers import EnsembleRetriever
from langchain_core.documents import Document
from collections import defaultdict


class HybridRetriever:
    """Combine Qdrant vector search with Neo4j graph traversal."""

    def __init__(
        self,
        qdrant_retriever,
        neo4j_graph: Neo4jGraph,
        reranker=None,
        llm=None,
    ):
        self.qdrant = qdrant_retriever
        self.neo4j = neo4j_graph
        self.reranker = reranker
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)

    async def ainvoke(self, query: str, top_k: int = 10) -> list[Document]:
        """Retrieve from both sources and fuse results."""
        import asyncio

        # Extract entities from query for graph search
        entities = await self._extract_entities(query)

        # Parallel retrieval
        qdrant_task = asyncio.create_task(
            self.qdrant.ainvoke(query)
        )

        # Graph query (sync, wrap in executor)
        loop = asyncio.get_event_loop()
        graph_task = loop.run_in_executor(
            None,
            lambda: graph_query_retriever(self.neo4j, entities)
        )

        qdrant_docs, graph_results = await asyncio.gather(qdrant_task, graph_task)

        # Convert graph results to documents
        graph_docs = [
            Document(
                page_content=r["content"],
                metadata={
                    "video_id": r["video_id"],
                    "video_title": r["video_title"],
                    "topics": r["topics"],
                    "source": "graph",
                }
            )
            for r in graph_results
        ]

        # Reciprocal Rank Fusion
        fused = self._reciprocal_rank_fusion([qdrant_docs, graph_docs])

        # Rerank if available
        if self.reranker:
            fused = self.reranker.rerank(query, fused, top_k=top_k)
        else:
            fused = fused[:top_k]

        return fused

    async def _extract_entities(self, query: str) -> list[str]:
        """Extract entities from query for graph search."""
        response = await self.llm.ainvoke(
            f"Extract key entities (topics, people, concepts) from this query. "
            f"Return as comma-separated list.\nQuery: {query}"
        )
        entities = [e.strip() for e in response.content.split(",")]
        return entities

    def _reciprocal_rank_fusion(
        self,
        results_lists: list[list[Document]],
        k: int = 60,
    ) -> list[Document]:
        """Fuse multiple ranked lists using RRF."""
        scores = defaultdict(float)
        doc_map = {}

        for results in results_lists:
            for rank, doc in enumerate(results):
                # Use content hash as ID
                doc_id = hash(doc.page_content)
                scores[doc_id] += 1.0 / (k + rank + 1)
                doc_map[doc_id] = doc

        # Sort by fused score
        sorted_ids = sorted(scores.keys(), key=lambda x: -scores[x])

        return [doc_map[doc_id] for doc_id in sorted_ids]


# Create the hybrid retriever (use in nodes.py)
hybrid_retriever = HybridRetriever(
    qdrant_retriever=create_qdrant_retriever(),
    neo4j_graph=create_neo4j_graph(
        url="bolt://neo4j.YOUR_TAILNET_DOMAIN.ts.net:7687",
        username="neo4j",
        password="...",
    ),
    reranker=FlashReranker(),
)
```

---

## Multi-Agent with LangGraph

### Supervisor Pattern

```python
# agents/supervisor.py
from typing import Literal
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent

from services.rag.state import RAGState


def create_supervisor_agent(llm: ChatOpenAI):
    """Create a supervisor that routes to specialist agents."""

    # Define specialist agents
    retriever_agent = create_react_agent(
        llm,
        tools=[qdrant_search_tool, neo4j_query_tool],
        prompt="You are a retrieval specialist. Find relevant documents.",
    )

    analyzer_agent = create_react_agent(
        llm,
        tools=[summarize_tool, extract_topics_tool],
        prompt="You are an analysis specialist. Analyze and summarize content.",
    )

    writer_agent = create_react_agent(
        llm,
        tools=[],  # No tools, just generates
        prompt="You are a writing specialist. Create clear, well-structured responses.",
    )

    # Supervisor decides which agent to call
    async def supervisor(state: RAGState) -> dict:
        """Route to appropriate specialist."""

        class Route(TypedDict):
            next: Literal["retriever", "analyzer", "writer", "end"]

        router_llm = llm.with_structured_output(Route)

        decision = await router_llm.ainvoke(
            f"""Based on the current state, decide the next step:

Question: {state['question']}
Documents retrieved: {len(state.get('documents', []))}
Analysis done: {bool(state.get('analysis'))}
Answer generated: {bool(state.get('generation'))}

Options:
- retriever: Need to fetch documents
- analyzer: Need to analyze retrieved documents
- writer: Ready to generate final answer
- end: Task complete
"""
        )

        return {"next": decision["next"]}

    # Build supervisor graph
    workflow = StateGraph(RAGState)

    workflow.add_node("supervisor", supervisor)
    workflow.add_node("retriever", retriever_agent)
    workflow.add_node("analyzer", analyzer_agent)
    workflow.add_node("writer", writer_agent)

    workflow.set_entry_point("supervisor")

    workflow.add_conditional_edges(
        "supervisor",
        lambda s: s["next"],
        {
            "retriever": "retriever",
            "analyzer": "analyzer",
            "writer": "writer",
            "end": END,
        }
    )

    # All specialists return to supervisor
    for agent in ["retriever", "analyzer", "writer"]:
        workflow.add_edge(agent, "supervisor")

    return workflow.compile()
```

### Router Pattern (Parallel Execution)

```python
# agents/router.py
from langgraph.graph import StateGraph, END
from langgraph.types import Send
import asyncio


async def route_query(state: RAGState) -> list[Send]:
    """Route query to appropriate retrievers in parallel."""

    query_type = await classify_query(state["question"])

    sends = []

    if query_type in ("factual", "hybrid"):
        sends.append(Send("vector_search", state))

    if query_type in ("relationship", "hybrid"):
        sends.append(Send("graph_search", state))

    return sends


async def classify_query(query: str) -> str:
    """Classify query type."""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    response = await llm.ainvoke(
        f"""Classify this query:
Query: {query}

Types:
- factual: Looking for specific information (use vector search)
- relationship: Asking about connections between things (use graph)
- hybrid: Complex query needing both

Return only the type."""
    )

    return response.content.strip().lower()


def create_router_agent():
    """Create router with parallel specialist execution."""

    workflow = StateGraph(RAGState)

    workflow.add_node("router", route_query)
    workflow.add_node("vector_search", vector_search_node)
    workflow.add_node("graph_search", graph_search_node)
    workflow.add_node("fuse", fuse_results)
    workflow.add_node("generate", generate)

    workflow.set_entry_point("router")

    # Router sends to specialists in parallel
    workflow.add_conditional_edges("router", lambda x: x)

    # Both specialists go to fuse
    workflow.add_edge("vector_search", "fuse")
    workflow.add_edge("graph_search", "fuse")

    # Fuse → generate → end
    workflow.add_edge("fuse", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()
```

---

## DeepAgents Integration

### Basic DeepAgent with RAG Tools

```python
# agents/youtube_rag.py
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool

from services.rag.retrievers import hybrid_retriever
from services.reranker import FlashReranker


# Define tools for DeepAgent
@tool
async def search_transcripts(query: str, top_k: int = 10) -> str:
    """Search YouTube video transcripts for relevant content.

    Args:
        query: The search query
        top_k: Number of results to return
    """
    docs = await hybrid_retriever.ainvoke(query, top_k=top_k)

    results = []
    for doc in docs:
        results.append(
            f"[{doc.metadata.get('video_title', 'Unknown')}]\n{doc.page_content[:500]}..."
        )

    return "\n\n---\n\n".join(results)


@tool
async def get_video_topics(video_id: str) -> str:
    """Get topics discussed in a specific video.

    Args:
        video_id: YouTube video ID
    """
    from services.rag.retrievers import create_neo4j_graph

    graph = create_neo4j_graph(...)

    result = graph.query(
        """
        MATCH (v:Video {id: $video_id})-[:MENTIONS]->(t:Topic)
        RETURN collect(t.name) as topics
        """,
        {"video_id": video_id}
    )

    return f"Topics: {', '.join(result[0]['topics'])}"


@tool
async def summarize_video(video_id: str) -> str:
    """Get a summary of a specific video.

    Args:
        video_id: YouTube video ID
    """
    # Retrieve all chunks for video
    docs = await hybrid_retriever.qdrant.ainvoke(
        "",  # Empty query
        filter={"video_id": video_id},
    )

    # Summarize with LLM
    llm = init_chat_model("openai:gpt-4o")

    full_text = "\n".join([d.page_content for d in docs])

    response = await llm.ainvoke(
        f"Summarize this video transcript:\n\n{full_text[:10000]}"
    )

    return response.content


# Create the DeepAgent
def create_youtube_rag_agent():
    """Create DeepAgent for YouTube RAG queries."""

    agent = create_deep_agent(
        model=init_chat_model("anthropic:claude-sonnet-4-6"),
        tools=[
            search_transcripts,
            get_video_topics,
            summarize_video,
        ],
        system_prompt="""You are a YouTube research assistant with access to a database of video transcripts.

Your capabilities:
1. Search transcripts for specific topics or questions
2. Get topics discussed in specific videos
3. Summarize video content

When answering questions:
- Always cite the video title and timestamp when possible
- If information isn't found, say so clearly
- Use the planning tool to break down complex research tasks
- Spawn subagents for parallel video analysis when needed
""",
    )

    return agent


# Usage
async def query_youtube_rag(question: str):
    agent = create_youtube_rag_agent()

    result = await agent.ainvoke({
        "messages": [{"role": "user", "content": question}]
    })

    return result["messages"][-1].content
```

### DeepAgent with Subagents

```python
# agents/research_agent.py
from deepagents import create_deep_agent
from langchain_core.tools import tool


@tool
async def spawn_video_analyzer(video_id: str, analysis_type: str) -> str:
    """Spawn a subagent to analyze a specific video.

    Args:
        video_id: YouTube video ID to analyze
        analysis_type: Type of analysis (summary, topics, sentiment, key_points)
    """
    # This creates an isolated subagent
    subagent = create_deep_agent(
        model=init_chat_model("openai:gpt-4o-mini"),
        tools=[search_transcripts, get_video_topics],
        system_prompt=f"You are analyzing video {video_id}. Focus on: {analysis_type}",
    )

    result = await subagent.ainvoke({
        "messages": [{
            "role": "user",
            "content": f"Analyze video {video_id} for {analysis_type}"
        }]
    })

    return result["messages"][-1].content


def create_research_agent():
    """Create a research agent that can spawn subagents."""

    return create_deep_agent(
        model=init_chat_model("anthropic:claude-sonnet-4-6"),
        tools=[
            search_transcripts,
            spawn_video_analyzer,  # Can spawn subagents
        ],
        system_prompt="""You are a research coordinator for YouTube video analysis.

For complex research tasks:
1. Use the planning tool to break down the task
2. Search for relevant videos
3. Spawn subagents to analyze individual videos in parallel
4. Synthesize findings into a comprehensive report

Use subagents when you need to analyze multiple videos - each subagent
handles one video, keeping context isolated and manageable.
""",
    )
```

---

## Caching Strategies

### Semantic Cache with Redis

```python
# services/cache.py
from redis import Redis
from langchain_openai import OpenAIEmbeddings
import numpy as np
import json


class SemanticCache:
    """Semantic caching for RAG queries."""

    def __init__(
        self,
        redis_client: Redis,
        embeddings: OpenAIEmbeddings,
        similarity_threshold: float = 0.95,
        ttl_seconds: int = 3600,
    ):
        self.redis = redis_client
        self.embeddings = embeddings
        self.threshold = similarity_threshold
        self.ttl = ttl_seconds
        self.index_name = "rag_cache"

    async def get(self, query: str) -> dict | None:
        """Check cache for similar query."""
        query_embedding = await self.embeddings.aembed_query(query)

        # Search Redis for similar embeddings
        # Using RediSearch vector similarity
        results = self.redis.ft(self.index_name).search(
            f"*=>[KNN 1 @embedding $vec AS score]",
            query_params={"vec": np.array(query_embedding).tobytes()},
        )

        if results.docs and float(results.docs[0].score) >= self.threshold:
            cached = json.loads(results.docs[0].response)
            return cached

        return None

    async def set(self, query: str, response: dict):
        """Cache query and response."""
        query_embedding = await self.embeddings.aembed_query(query)

        cache_key = f"rag_cache:{hash(query)}"

        self.redis.hset(cache_key, mapping={
            "query": query,
            "embedding": np.array(query_embedding).tobytes(),
            "response": json.dumps(response),
        })

        self.redis.expire(cache_key, self.ttl)


# Multi-tier cache wrapper
class MultiTierCache:
    """Three-tier caching: semantic → retrieval → embedding."""

    def __init__(self, redis_client: Redis, embeddings):
        self.semantic_cache = SemanticCache(redis_client, embeddings)
        self.redis = redis_client

    async def get_or_compute(
        self,
        query: str,
        compute_fn,
    ) -> dict:
        """Check all cache tiers before computing."""

        # Tier 1: Semantic cache (exact/similar query)
        cached = await self.semantic_cache.get(query)
        if cached:
            return {**cached, "cache_hit": "semantic"}

        # Tier 2: Retrieval cache (reuse retrieved docs)
        query_hash = hash(query)
        retrieval_key = f"retrieval:{query_hash}"
        cached_docs = self.redis.get(retrieval_key)

        if cached_docs:
            # Still need to generate, but skip retrieval
            result = await compute_fn(query, cached_docs=json.loads(cached_docs))
            return {**result, "cache_hit": "retrieval"}

        # Cache miss: full computation
        result = await compute_fn(query)

        # Cache the result
        await self.semantic_cache.set(query, result)
        self.redis.setex(
            retrieval_key,
            3600,
            json.dumps([d.dict() for d in result.get("documents", [])])
        )

        return {**result, "cache_hit": None}
```

---

## Evaluation with RAGAS

### Setting Up Evaluation

```python
# services/evaluation.py
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
)
from datasets import Dataset


async def evaluate_rag_pipeline(
    test_questions: list[str],
    rag_agent,
    ground_truths: list[str] = None,
) -> dict:
    """Evaluate RAG pipeline using RAGAS."""

    # Collect predictions
    predictions = []

    for question in test_questions:
        result = await rag_agent.ainvoke({"question": question})

        predictions.append({
            "question": question,
            "answer": result["generation"],
            "contexts": [d.page_content for d in result["documents"]],
            "ground_truth": ground_truths[len(predictions)] if ground_truths else "",
        })

    # Create dataset
    dataset = Dataset.from_list(predictions)

    # Run evaluation
    metrics = [faithfulness, answer_relevancy, context_precision]
    if ground_truths:
        metrics.append(context_recall)

    results = evaluate(dataset, metrics=metrics)

    return results.to_pandas().to_dict()


# Build test dataset from real queries
def build_test_dataset_from_logs(
    log_file: str,
    sample_size: int = 100,
) -> list[dict]:
    """Extract test cases from query logs."""
    import random

    with open(log_file) as f:
        logs = [json.loads(line) for line in f]

    # Sample queries
    samples = random.sample(logs, min(sample_size, len(logs)))

    return [
        {
            "question": log["query"],
            "ground_truth": log.get("expected_answer", ""),
        }
        for log in samples
    ]
```

### Continuous Evaluation

```python
# services/evaluation.py (continued)

class RAGEvaluator:
    """Continuous RAG evaluation with alerting."""

    def __init__(
        self,
        rag_agent,
        thresholds: dict = None,
    ):
        self.agent = rag_agent
        self.thresholds = thresholds or {
            "faithfulness": 0.85,
            "answer_relevancy": 0.80,
            "context_precision": 0.70,
        }

    async def evaluate_single(self, question: str, answer: str, contexts: list[str]) -> dict:
        """Evaluate a single query-answer pair."""
        from ragas.metrics import faithfulness, answer_relevancy

        dataset = Dataset.from_list([{
            "question": question,
            "answer": answer,
            "contexts": contexts,
        }])

        results = evaluate(dataset, metrics=[faithfulness, answer_relevancy])

        scores = results.to_pandas().iloc[0].to_dict()

        # Check against thresholds
        alerts = []
        for metric, threshold in self.thresholds.items():
            if scores.get(metric, 1.0) < threshold:
                alerts.append(f"{metric}: {scores[metric]:.2f} < {threshold}")

        return {
            "scores": scores,
            "alerts": alerts,
            "passed": len(alerts) == 0,
        }
```

---

## Complete Example: YouTube RAG Agent

```python
# agents/youtube_rag_complete.py
"""
Complete YouTube RAG Agent using DeepAgents + LangGraph + LangChain.
This is the main entry point for your RAG system.
"""
from fastapi import APIRouter, Request
from langchain.chat_models import init_chat_model
from deepagents import create_deep_agent
from langchain_core.tools import tool

from services.rag.graph import create_rag_graph
from services.rag.retrievers import HybridRetriever, create_qdrant_retriever, create_neo4j_graph
from services.reranker import FlashReranker
from services.cache import MultiTierCache
from services.evaluation import RAGEvaluator

router = APIRouter()


# === TOOLS ===
@tool
async def search_videos(query: str, max_results: int = 10) -> str:
    """Search YouTube video transcripts.

    Args:
        query: Search query
        max_results: Maximum number of results
    """
    retriever = get_hybrid_retriever()
    docs = await retriever.ainvoke(query, top_k=max_results)

    return format_search_results(docs)


@tool
async def get_video_details(video_id: str) -> str:
    """Get detailed information about a video.

    Args:
        video_id: YouTube video ID
    """
    # Query your ElasticSearch for video metadata
    # (Uses your existing infrastructure)
    pass


@tool
async def analyze_topic_across_videos(topic: str) -> str:
    """Analyze how a topic is discussed across multiple videos.

    Args:
        topic: Topic to analyze
    """
    graph = get_neo4j_graph()

    result = graph.query("""
        MATCH (t:Topic {name: $topic})<-[:MENTIONS]-(v:Video)
        WITH v
        MATCH (v)-[:MENTIONS]->(related:Topic)
        WHERE related.name <> $topic
        RETURN v.title as video, collect(DISTINCT related.name) as related_topics
        LIMIT 10
    """, {"topic": topic})

    return format_topic_analysis(result)


# === AGENT CREATION ===
def create_youtube_agent(checkpointer=None):
    """Create the main YouTube RAG agent."""

    # Option 1: Use DeepAgents for complex tasks
    deep_agent = create_deep_agent(
        model=init_chat_model("anthropic:claude-sonnet-4-6"),
        tools=[search_videos, get_video_details, analyze_topic_across_videos],
        system_prompt="""You are a YouTube research assistant with access to a database
of video transcripts from various channels.

When answering questions:
1. Search for relevant content first
2. Cite video titles and timestamps
3. Use the planning tool for complex multi-step research
4. Spawn subagents for parallel video analysis when needed
""",
    )

    # Option 2: Use LangGraph for structured RAG workflow
    rag_graph = create_rag_graph(checkpointer)

    return {
        "deep_agent": deep_agent,
        "rag_graph": rag_graph,
    }


# === FASTAPI ENDPOINTS ===
@router.post("/query")
async def query(question: str, mode: str = "rag", request: Request):
    """Query the YouTube RAG system.

    Args:
        question: User question
        mode: "rag" for structured workflow, "agent" for DeepAgents
    """
    agents = create_youtube_agent(request.app.state.checkpointer)
    cache = MultiTierCache(request.app.state.redis_aio, get_embeddings())

    # Check cache first
    async def compute(q, cached_docs=None):
        if mode == "agent":
            result = await agents["deep_agent"].ainvoke({
                "messages": [{"role": "user", "content": q}]
            })
            return {"generation": result["messages"][-1].content}
        else:
            return await agents["rag_graph"].ainvoke({
                "question": q,
                "documents": cached_docs or [],
                "generation": "",
                "retry_count": 0,
                "messages": [],
            })

    result = await cache.get_or_compute(question, compute)

    # Evaluate quality
    evaluator = RAGEvaluator(agents["rag_graph"])
    evaluation = await evaluator.evaluate_single(
        question,
        result["generation"],
        [d.page_content for d in result.get("documents", [])],
    )

    return {
        "question": question,
        "answer": result["generation"],
        "sources": result.get("documents", []),
        "cache_hit": result.get("cache_hit"),
        "quality": evaluation["scores"],
        "quality_passed": evaluation["passed"],
    }


@router.post("/research")
async def research(topic: str, depth: str = "standard", request: Request):
    """Deep research on a topic using DeepAgents planning."""

    agent = create_deep_agent(
        model=init_chat_model("anthropic:claude-sonnet-4-6"),
        tools=[search_videos, analyze_topic_across_videos],
        system_prompt="You are a research coordinator. Break down complex research into steps.",
    )

    result = await agent.ainvoke({
        "messages": [{
            "role": "user",
            "content": f"Research the topic '{topic}' across our video database. "
                      f"Depth level: {depth}. "
                      f"Create a comprehensive report with citations."
        }]
    })

    return {
        "topic": topic,
        "report": result["messages"][-1].content,
    }
```

---

## Production Checklist

### Infrastructure
- [ ] Qdrant cluster with replication
- [ ] Neo4j cluster (or AuraDB)
- [ ] Redis for caching + LangGraph checkpointing
- [ ] Load balancer for API
- [ ] Monitoring (Prometheus, Grafana)

### Retrieval Quality
- [ ] Chunking: `RecursiveCharacterTextSplitter(chunk_size=512, overlap=50)`
- [ ] Embeddings: BGE-M3 (self-hosted) or OpenAI (API)
- [ ] Reranker: FlashRank or Cohere Rerank
- [ ] Hybrid retrieval: Qdrant + Neo4j with RRF fusion

### Agent Quality
- [ ] LangGraph workflow with grade → rewrite → retry loop
- [ ] Hallucination checking node
- [ ] Max 3 retries before failing
- [ ] DeepAgents for complex multi-step tasks

### Observability
- [ ] RAGAS evaluation pipeline
- [ ] Faithfulness > 0.85 threshold alerting
- [ ] Latency tracking per component
- [ ] Cache hit rate monitoring

### Cost Optimization
- [ ] Multi-tier caching (semantic → retrieval → embedding)
- [ ] Prompt caching with DeepAgents
- [ ] MRL embeddings (truncate to 256 dims for speed)
- [ ] Token usage monitoring

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- [ ] `services/rag/state.py` - Define RAGState
- [ ] `services/rag/nodes.py` - Implement retrieve, grade, generate, rewrite
- [ ] `services/rag/graph.py` - Build LangGraph workflow
- [ ] `services/chunker.py` - RecursiveCharacterTextSplitter

### Phase 2: Hybrid Retrieval (Week 3-4)
- [ ] `services/rag/retrievers.py` - QdrantVectorStore setup
- [ ] Neo4j schema design + LLMGraphTransformer
- [ ] HybridRetriever with RRF fusion
- [ ] Integration tests

### Phase 3: Reranking + Evaluation (Week 5-6)
- [ ] `services/reranker.py` - FlashRank integration
- [ ] `services/evaluation.py` - RAGAS setup
- [ ] Build test dataset from real queries
- [ ] Tune retrieval parameters (top_k, threshold)

### Phase 4: DeepAgents (Week 7-8)
- [ ] `agents/youtube_rag.py` - Define tools
- [ ] Planning + subagent spawning
- [ ] FastAPI endpoints

### Phase 5: Production (Week 9-10)
- [ ] `services/cache.py` - Multi-tier caching
- [ ] Monitoring dashboards
- [ ] Load testing
- [ ] Documentation

---

## Sources

### LangGraph & Agentic RAG
- [LangChain Docs: Agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)
- [Building Agentic RAG with LangGraph 2026](https://rahulkolekar.com/building-agentic-rag-systems-with-langgraph/)
- [Corrective RAG (CRAG) with LangGraph](https://www.datacamp.com/tutorial/corrective-rag-crag)

### DeepAgents
- [GitHub: langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)
- [Deep Agents Overview - LangChain Docs](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Tutorial - DataCamp](https://www.datacamp.com/tutorial/deep-agents)

### Qdrant + Neo4j
- [LangChain Qdrant Integration](https://docs.langchain.com/oss/python/integrations/vectorstores/qdrant)
- [QdrantVectorStore API](https://api.python.langchain.com/en/latest/qdrant/langchain_qdrant.qdrant.QdrantVectorStore.html)
- [GraphRAG with Qdrant and Neo4j](https://qdrant.tech/documentation/examples/graphrag-qdrant-neo4j/)
- [LLMGraphTransformer Guide](https://neo4j.com/blog/developer/global-graphrag-neo4j-langchain/)

### Evaluation
- [RAGAS Documentation](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)
- [RAG Evaluation Best Practices - Qdrant](https://qdrant.tech/blog/rag-evaluation-guide/)

---

*Last updated: 2026-04-04*
