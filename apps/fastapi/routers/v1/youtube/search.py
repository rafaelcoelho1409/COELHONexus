import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from langchain_neo4j import Neo4jGraph, Neo4jVector
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_experimental.graph_transformers import LLMGraphTransformer

from schemas.inputs import YouTubeSearchConfig


router = APIRouter()


# =============================================================================
# Neo4j Configuration
# =============================================================================
NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USERNAME = os.environ["NEO4J_USERNAME"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]


# =============================================================================
# Singleton instances (initialized on first use)
# =============================================================================
_neo4j_graph: Neo4jGraph | None = None
_vector_index: Neo4jVector | None = None
_llm_transformer: LLMGraphTransformer | None = None
_embeddings: HuggingFaceEmbeddings | None = None


def get_embeddings() -> HuggingFaceEmbeddings:
    """Get or create HuggingFace embeddings (singleton)."""
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name = "all-MiniLM-L6-v2")
    return _embeddings


def get_neo4j_graph() -> Neo4jGraph:
    """Get or create Neo4j graph connection (singleton)."""
    global _neo4j_graph
    if _neo4j_graph is None:
        _neo4j_graph = Neo4jGraph(
            url = NEO4J_URI,
            username = NEO4J_USERNAME,
            password = NEO4J_PASSWORD,
        )
    return _neo4j_graph


def get_vector_index() -> Neo4jVector:
    """Get or create Neo4j vector index with hybrid search (singleton)."""
    global _vector_index
    if _vector_index is None:
        _vector_index = Neo4jVector.from_existing_graph(
            embedding = get_embeddings(),
            url = NEO4J_URI,
            username = NEO4J_USERNAME,
            password = NEO4J_PASSWORD,
            search_type = "hybrid",
            node_label = "Document",
            text_node_properties = ["text"],
            embedding_node_property = "embedding",
        )
    return _vector_index


def get_llm_transformer(
    provider: str = "NVIDIA",
    model_name: str = "openai/gpt-oss-120b",
) -> LLMGraphTransformer:
    """Get or create LLM graph transformer (singleton)."""
    global _llm_transformer
    if _llm_transformer is None:
        llm = model_selector.get_model(
            provider = provider,
            model_name = model_name,
            temperature = 0.0,
        )
        _llm_transformer = LLMGraphTransformer(llm = llm)
    return _llm_transformer


# =============================================================================
# Response Models
# =============================================================================
class ModelLoadResponse(BaseModel):
    status: str
    neo4j_connected: bool
    vector_index_ready: bool
    transformer_ready: bool
    embedding_model: str


class GraphClearResponse(BaseModel):
    status: str
    message: str



# =============================================================================
# Endpoints
# =============================================================================
@router.put("/config")
async def update_youtube_search_config(config: YouTubeSearchConfig, request: Request):
    redis_aio = request.app.state.redis_aio
    await redis_aio.json().set(
        "youtube:search:config", 
        "$", 
        config.model_dump(exclude_none = True)
    )
    return {
        "status": "saved", 
        "config": config.model_dump(
            exclude_none = True)}


@router.get("/load", response_model = ModelLoadResponse)
def load_model():
    """
    Initialize Neo4j graph, vector index, and LLM transformer.
    This replaces the old /youtube_content_search/load_model endpoint.
    """
    try:
        # Initialize all components
        graph = get_neo4j_graph()
        vector_index = get_vector_index()
        transformer = get_llm_transformer()
        return ModelLoadResponse(
            status = "success",
            neo4j_connected = True,
            vector_index_ready = True,
            transformer_ready = True,
            embedding_model = "all-MiniLM-L6-v2",
        )
    except Exception as e:
        raise HTTPException(
            status_code = 500, 
            detail = f"Failed to load model: {str(e)}")


@router.post("/clear-graph", response_model = GraphClearResponse)
def clear_neo4j_graph():
    """Clear all nodes and relationships from Neo4j graph."""
    try:
        graph = get_neo4j_graph()
        graph.query("MATCH (n) DETACH DELETE n")
        return GraphClearResponse(
            status = "success",
            message = "Neo4j graph cleared successfully",
        )
    except Exception as e:
        raise HTTPException(
            status_code = 500, 
            detail = f"Failed to clear graph: {str(e)}")


@router.get("/health")
def health_check():
    """Check Neo4j connection health."""
    try:
        graph = get_neo4j_graph()
        result = graph.query("RETURN 1 as ping")
        return {"status": "healthy", "neo4j": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "neo4j": str(e)}
