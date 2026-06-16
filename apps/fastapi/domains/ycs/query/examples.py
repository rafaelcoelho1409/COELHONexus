"""ycs/query — curated few-shot Q→DSL pairs per backend.

Per the Neo4j Text2Cypher Guide (Feb 2026): "Few-shot learning uses
similar examples stored in a vector database for Cypher query
generation." For a v1 we hard-code 4–6 high-coverage exemplars per
backend; phase 4.x will swap this for a Qdrant collection
(`query_examples`) with embedding-based retrieval. Today the
generator dumps the first N exemplars into the prompt verbatim.

Each pair carries:
  · question — the natural-language ask
  · query    — the DSL string the model should learn to produce

Keep `query` strings runnable as-is (the editor will be populated
with the raw text + the model fills the parameters). When you add an
exemplar, eyeball it against the live store to confirm it returns
hits — a stale example teaches the model wrong shapes."""
from __future__ import annotations

from .params import BACKEND_ES, BACKEND_NEO4J, BACKEND_QDRANT


ES_EXAMPLES: list[dict[str, str]] = [
    {
        "question": "show me the 10 most-viewed videos",
        "query": (
            '{\n'
            '  "query": { "match_all": {} },\n'
            '  "sort":  [{ "view_count": "desc" }],\n'
            '  "size":  10\n'
            '}'
        ),
    },
    {
        "question": "find transcripts mentioning attention mechanisms",
        "query": (
            '{\n'
            '  "query": {\n'
            '    "multi_match": {\n'
            '      "query":  "attention mechanism",\n'
            '      "fields": ["content"],\n'
            '      "type":   "best_fields"\n'
            '    }\n'
            '  },\n'
            '  "size": 20\n'
            '}'
        ),
    },
    {
        "question": "search a channel's videos for the word transformer",
        "query": (
            '{\n'
            '  "query": {\n'
            '    "bool": {\n'
            '      "must":   { "multi_match": { "query": "transformer", "fields": ["title^3", "description", "content"] } },\n'
            '      "filter": { "term": { "channel_id": "UC...replace_me..." } }\n'
            '    }\n'
            '  },\n'
            '  "size": 25\n'
            '}'
        ),
    },
    {
        "question": "aggregate videos by channel and return counts",
        "query": (
            '{\n'
            '  "size": 0,\n'
            '  "aggs": {\n'
            '    "by_channel": {\n'
            '      "terms": { "field": "channel_id", "size": 30 }\n'
            '    }\n'
            '  }\n'
            '}'
        ),
    },
]


QDRANT_EXAMPLES: list[dict[str, str]] = [
    {
        "question": "browse the first 20 chunks in the collection",
        "query": (
            '{\n'
            '  "op":            "scroll",\n'
            '  "limit":         20,\n'
            '  "with_payload":  true\n'
            '}'
        ),
    },
    {
        "question": "count points in the collection",
        "query": '{ "op": "count", "exact": true }',
    },
    {
        "question": "filter scroll to one video_id",
        "query": (
            '{\n'
            '  "op": "scroll",\n'
            '  "limit": 50,\n'
            '  "with_payload": true,\n'
            '  "scroll_filter": {\n'
            '    "must": [\n'
            '      { "key": "video_id", "match": { "value": "REPLACE_VIDEO_ID" } }\n'
            '    ]\n'
            '  }\n'
            '}'
        ),
    },
    {
        "question": "filter scroll to chunks belonging to ANY of two channels",
        "query": (
            '{\n'
            '  "op": "scroll",\n'
            '  "limit": 50,\n'
            '  "with_payload": true,\n'
            '  "scroll_filter": {\n'
            '    "must": [\n'
            '      { "key": "channel_id", "match": { "any": ["UC...id1", "UC...id2"] } }\n'
            '    ]\n'
            '  }\n'
            '}'
        ),
    },
    {
        "question": "scroll excluding a specific channel",
        "query": (
            '{\n'
            '  "op": "scroll",\n'
            '  "limit": 50,\n'
            '  "with_payload": true,\n'
            '  "scroll_filter": {\n'
            '    "must_not": [\n'
            '      { "key": "channel_id", "match": { "value": "UC...id1" } }\n'
            '    ]\n'
            '  }\n'
            '}'
        ),
    },
    {
        # This is the negative example the user-reported bug needs —
        # the LLM tried `match: {text: "Brasil"}` on `content`, which
        # Pydantic rejects because the field has no text index. Show
        # the LLM what to do in that case: drop the filter, return a
        # browse-scroll, let the user pivot to Elasticsearch.
        "question": "find chunks where content contains a substring (no text index on content)",
        "query": (
            '{\n'
            '  "op":           "scroll",\n'
            '  "limit":        100,\n'
            '  "with_payload": true\n'
            '}'
        ),
    },
]


NEO4J_EXAMPLES: list[dict[str, str]] = [
    {
        "question": "list 10 videos with title and url",
        "query": (
            "MATCH (v:Video)\n"
            "RETURN v.title AS title, v.webpage_url AS url\n"
            "LIMIT 10"
        ),
    },
    {
        "question": "count documents per channel",
        "query": (
            "MATCH (d:Document)\n"
            "WITH d.channel_id AS channel_id, count(d) AS n\n"
            "RETURN channel_id, n\n"
            "ORDER BY n DESC\n"
            "LIMIT 25"
        ),
    },
    {
        "question": "find entities most-mentioned across videos",
        "query": (
            "MATCH (d:Document)-[r:MENTIONS]->(e:__Entity__)\n"
            "WITH e, count(DISTINCT d.video_id) AS videos\n"
            "RETURN e.id AS entity, videos\n"
            "ORDER BY videos DESC\n"
            "LIMIT 25"
        ),
    },
    {
        "question": "find entities co-mentioned with X",
        "query": (
            'MATCH (d:Document)-[:MENTIONS]->(target:__Entity__ {id: "REPLACE_ENTITY"})\n'
            "MATCH (d)-[:MENTIONS]->(other:__Entity__)\n"
            "WHERE other <> target\n"
            "WITH other, count(DISTINCT d) AS co\n"
            "RETURN other.id AS co_entity, co\n"
            "ORDER BY co DESC\n"
            "LIMIT 20"
        ),
    },
]


EXAMPLES_BY_BACKEND: dict[str, list[dict[str, str]]] = {
    BACKEND_ES:     ES_EXAMPLES,
    BACKEND_QDRANT: QDRANT_EXAMPLES,
    BACKEND_NEO4J:  NEO4J_EXAMPLES,
}
