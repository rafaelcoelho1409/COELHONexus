"""
Neo4j Knowledge Graph Schema

CONCEPT: A knowledge graph stores ENTITIES (nodes) and RELATIONSHIPS (edges).
Unlike a vector store that answers "what text is similar?", a knowledge graph
answers "how are things connected?" — enabling multi-hop reasoning.

Example: "What topics does Andrej Karpathy discuss that are also discussed by 3Blue1Brown?"
- Vector search: can't answer this (no concept of entities or connections)
- Graph traversal:
    (Speaker:Karpathy)-[:DISCUSSES]->(Topic:Transformers)<-[:DISCUSSES]-(Speaker:3Blue1Brown)

Schema design follows the architecture doc:
- Video: a YouTube video
- Channel: a YouTube channel
- Topic: a subject/concept extracted from transcripts
- Speaker: a person mentioned or speaking in a video
- Segment: a chunk of transcript text

Relationships encode how entities connect:
- BELONGS_TO: Video → Channel
- DISCUSSES: Video/Speaker → Topic
- PART_OF: Segment → Video
- RELATED_TO: Topic → Topic (semantic similarity)
- MENTIONS: Segment → Speaker/Topic

These Pydantic models are for documentation and API responses.
The actual Neo4j schema is created by LLMGraphTransformer + Cypher queries.
"""
from pydantic import BaseModel


# =============================================================================
# Node Models (for API responses and documentation)
# =============================================================================
class VideoNode(BaseModel):
    """A YouTube video in the knowledge graph."""
    id: str                         # YouTube video ID
    title: str
    channel_id: str | None = None
    channel: str | None = None
    upload_date: str | None = None
    webpage_url: str | None = None


class ChannelNode(BaseModel):
    """A YouTube channel."""
    id: str                         # Channel ID
    name: str


class TopicNode(BaseModel):
    """A topic/concept extracted from video transcripts."""
    name: str
    description: str | None = None


class SpeakerNode(BaseModel):
    """A person mentioned or speaking in videos."""
    name: str


# =============================================================================
# Graph Statistics (for /graph/stats endpoint)
# =============================================================================
class GraphStats(BaseModel):
    """Summary statistics for the knowledge graph."""
    total_nodes: int = 0
    total_relationships: int = 0
    nodes_by_label: dict[str, int] = {}
    relationships_by_type: dict[str, int] = {}


# =============================================================================
# Allowed nodes and relationships for LLMGraphTransformer
# =============================================================================
# These constrain what the LLM can extract — without constraints,
# the LLM invents arbitrary entity types and the graph becomes messy.

ALLOWED_NODES = ["Video", "Channel", "Topic", "Person", "Concept", "Technology"]

ALLOWED_RELATIONSHIPS = [
    "DISCUSSES",     # Video/Person → Topic/Concept/Technology
    "BELONGS_TO",    # Video → Channel
    "MENTIONS",      # Video → Person/Technology
    "RELATED_TO",    # Topic → Topic, Concept → Concept
    "FEATURES",      # Video → Person (appears in video)
    "USES",          # Person/Video → Technology
]
