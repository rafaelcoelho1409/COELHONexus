"""
Knowledge Graph Builder — LLMGraphTransformer → Neo4j

CONCEPT: LLMGraphTransformer sends each document to the LLM with a prompt
asking it to extract entities and relationships. The LLM returns structured
data (via function calling) that LangChain converts into GraphDocument objects.

GraphDocument has:
  - nodes: list of Node(id, type, properties)
  - relationships: list of Relationship(source, target, type, properties)
  - source: the original Document

These are then stored in Neo4j via graph.add_graph_documents().

COST WARNING: Each chunk requires an LLM call for entity extraction.
For 100 chunks, that's 100 LLM calls. Use aconvert_to_graph_documents()
for parallel processing, and consider running this on a cheaper model.

Flow:
  Transcript chunks (from ES or Qdrant)
    → LLMGraphTransformer extracts entities + relationships
    → Neo4j stores the knowledge graph
    → Cypher queries traverse the graph for retrieval
"""
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_neo4j import Neo4jGraph

from schemas.graph import ALLOWED_NODES, ALLOWED_RELATIONSHIPS


def create_graph_transformer(llm: ChatOpenAI) -> LLMGraphTransformer:
    """
    Create an LLMGraphTransformer with constrained entity/relationship types.

    CONCEPT: allowed_nodes and allowed_relationships CONSTRAIN the LLM.
    Without them, the LLM invents arbitrary types like "Event", "Date",
    "Abstract_Concept_Type_3" — making the graph inconsistent and hard to query.

    With constraints:
      - LLM can only create nodes of types: Video, Channel, Topic, Person, etc.
      - LLM can only create relationships of types: DISCUSSES, MENTIONS, etc.
      - Graph stays clean and queryable with predictable Cypher patterns
    """
    return LLMGraphTransformer(
        llm = llm,
        allowed_nodes = ALLOWED_NODES,
        allowed_relationships = ALLOWED_RELATIONSHIPS,
        node_properties = True,     # Extract all properties the LLM finds
        relationship_properties = True,
        strict_mode = True,         # Only allow defined types (reject others)
    )


async def extract_and_store_graph(
    documents: list[Document],
    llm: ChatOpenAI,
    neo4j_graph: Neo4jGraph,
    batch_size: int = 10,
) -> dict:
    """
    Extract entities from documents and store in Neo4j.

    CONCEPT: aconvert_to_graph_documents() processes documents in PARALLEL.
    Each document gets its own LLM call, but they all run concurrently.
    This is much faster than sequential processing for large batches.

    The batch_size limits how many concurrent LLM calls we make at once
    to avoid rate limiting and memory issues.

    Returns: {documents_processed, nodes_created, relationships_created}
    """
    transformer = create_graph_transformer(llm)
    total_nodes = 0
    total_relationships = 0
    total_processed = 0
    # Process in batches to avoid overwhelming the LLM API
    for batch_start in range(0, len(documents), batch_size):
        batch = documents[batch_start:batch_start + batch_size]
        # Async parallel extraction — all docs in batch processed concurrently
        graph_documents = await transformer.aconvert_to_graph_documents(batch)
        # Store in Neo4j
        # include_source=True creates a Document node linked to extracted entities
        # This lets us trace back from any entity to its source transcript chunk
        neo4j_graph.add_graph_documents(
            graph_documents,
            include_source = True,
            baseEntityLabel = True,  # Add __Entity__ label to all nodes for unified queries
        )
        # Count what was created
        for gdoc in graph_documents:
            total_nodes += len(gdoc.nodes)
            total_relationships += len(gdoc.relationships)
        total_processed += len(batch)
    return {
        "documents_processed": total_processed,
        "nodes_created": total_nodes,
        "relationships_created": total_relationships,
    }


async def get_graph_stats(neo4j_graph: Neo4jGraph) -> dict:
    """
    Get node and relationship counts from Neo4j.

    CONCEPT: Cypher is Neo4j's query language.
    - MATCH (n) — find all nodes
    - labels(n) — get the labels (types) of a node
    - type(r) — get the type of a relationship
    - UNWIND — flatten a list into rows
    """
    # Count nodes by label
    nodes_result = neo4j_graph.query(
        "MATCH (n) "
        "UNWIND labels(n) AS label "
        "RETURN label, count(*) AS count "
        "ORDER BY count DESC"
    )
    nodes_by_label = {row["label"]: row["count"] for row in nodes_result}
    # Count relationships by type
    rels_result = neo4j_graph.query(
        "MATCH ()-[r]->() "
        "RETURN type(r) AS type, count(*) AS count "
        "ORDER BY count DESC"
    )
    rels_by_type = {row["type"]: row["count"] for row in rels_result}
    return {
        "total_nodes": sum(nodes_by_label.values()),
        "total_relationships": sum(rels_by_type.values()),
        "nodes_by_label": nodes_by_label,
        "relationships_by_type": rels_by_type,
    }


def build_video_metadata_graph(
    neo4j_graph: Neo4jGraph,
    videos: list[dict],
):
    """
    Create Video and Channel nodes from metadata (no LLM needed).

    CONCEPT: Not everything needs LLM extraction. Video metadata
    (title, channel, upload_date) is structured data — we can create
    graph nodes directly from it with Cypher MERGE statements.

    MERGE is idempotent: if the node already exists, it updates it.
    If not, it creates it. Safe to run multiple times.
    """
    for video in videos:
        # Create/update Video node
        neo4j_graph.query(
            "MERGE (v:Video {id: $id}) "
            "SET v.title = $title, "
            "    v.upload_date = $upload_date, "
            "    v.webpage_url = $webpage_url",
            params = {
                "id": video.get("video_id", ""),
                "title": video.get("title", ""),
                "upload_date": video.get("upload_date", ""),
                "webpage_url": video.get("webpage_url", ""),
            },
        )
        # Create/update Channel node + relationship
        channel = video.get("channel", "")
        channel_id = video.get("channel_id", "")
        if channel and channel_id:
            neo4j_graph.query(
                "MERGE (c:Channel {id: $channel_id}) "
                "SET c.name = $channel_name "
                "WITH c "
                "MATCH (v:Video {id: $video_id}) "
                "MERGE (v)-[:BELONGS_TO]->(c)",
                params = {
                    "channel_id": channel_id,
                    "channel_name": channel,
                    "video_id": video.get("video_id", ""),
                },
            )
