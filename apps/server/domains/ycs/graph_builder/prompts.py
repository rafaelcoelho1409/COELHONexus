"""ycs/graph_builder — extraction instructions + schema-discovery prompt.
Version marker per `docs/CODE-CONVENTIONS.md` §2."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


GRAPH_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


# Sent to `LLMGraphTransformer` as `additional_instructions`. Enforces
# FORMATTING (TitleCase nodes, UPPER_SNAKE_CASE relationships,
# normalized names) — NOT a content constraint. Works for any YouTube
# topic.
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


SCHEMA_DISCOVERY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a knowledge graph schema designer. Analyze the sample "
        "transcripts and suggest the most useful entity types and "
        "relationship types for building a knowledge graph. Focus on "
        "types that enable multi-hop reasoning and cross-document "
        "connections. Return 5-8 node types and 6-10 relationship types.",
    ),
    (
        "human",
        "Sample transcripts:\n\n{samples}\n\nSuggest the best schema:",
    ),
])
