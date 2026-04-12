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


# =============================================================================
# Extraction Instructions — No schema constraints, format-guided
# =============================================================================
# NO allowed_nodes or allowed_relationships constraints.
# The LLM captures ALL entities and relationships it finds.
# Instructions enforce consistent FORMATTING, not content limits.
# This works across ANY YouTube channel topic (finance, tech, cooking, etc.)

EXTRACTION_INSTRUCTIONS = """
Extract ALL entities and relationships from the text. Do not limit yourself
to predefined types — capture everything meaningful.

FORMATTING RULES (critical for graph consistency):
- Node labels: use TitleCase singular nouns (e.g., Country, Person, Organization,
  Technology, Concept, Product, Event, Law, Program, City)
- Relationship types: use UPPER_SNAKE_CASE verbs (e.g., DISCUSSES, RECOMMENDS,
  LOCATED_IN, WARNS_AGAINST, COSTS, RELATED_TO, MENTIONS, FEATURES, USES)
- Entity IDs: use the most complete, official form of the name
  - Countries: official full names ("Saint Kitts and Nevis" not "St Kitts")
  - People: full names when available ("Rafael Cintron" not "Rafael")
  - Organizations: official names ("Goldman Sachs" not "Goldman")
- Money amounts: normalize to numbers ("$100,000" not "$100K" or "100 thousand")
- Prefer general relationship types when possible (DISCUSSES over TALKS_ABOUT)
- Merge obvious aliases (e.g., "the UAE" and "United Arab Emirates" → same entity)

WHAT TO EXTRACT:
- Every person, organization, country, city, concept, product, technology,
  event, law, program, or notable entity mentioned
- Every relationship between entities: who recommends what, who warns against
  what, what costs how much, what is located where, what is related to what
- Opinions and stances: if the speaker recommends or warns against something,
  capture that as a relationship (RECOMMENDS or WARNS_AGAINST)
"""
