"""
Neo4j Knowledge Graph Schema

CONCEPT: A knowledge graph stores ENTITIES (nodes) and RELATIONSHIPS (edges).
Unlike a vector store that answers "what text is similar?", a knowledge graph
answers "how are things connected?" — enabling multi-hop reasoning.

Schema is auto-discoverable per channel via discover_schema(), but we provide
a strong default schema optimized for YouTube content about expat/investment topics.
The schema can be overridden for different content domains.
"""
from pydantic import BaseModel


# =============================================================================
# Node Models (for API responses and documentation)
# =============================================================================
class VideoNode(BaseModel):
    """A YouTube video in the knowledge graph."""
    id: str
    title: str
    channel_id: str | None = None
    channel: str | None = None
    upload_date: str | None = None
    webpage_url: str | None = None


class ChannelNode(BaseModel):
    """A YouTube channel."""
    id: str
    name: str


class GraphStats(BaseModel):
    """Summary statistics for the knowledge graph."""
    total_nodes: int = 0
    total_relationships: int = 0
    nodes_by_label: dict[str, int] = {}
    relationships_by_type: dict[str, int] = {}
