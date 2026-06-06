"""ycs/rag/adaptive/nodes/classify — CLASSIFY node + channel auto-detection.

Entry-routing node. Honors `force_mode` when the caller already picked
a path (e.g. "force standard" for a debug query) — otherwise asks the
LLM. Also pulls channel/person names out of the question and resolves
them to `channel_id`s via a Neo4j Cypher call.

`_resolve_channel_ids` is the I/O helper for the auto-scope path —
deprecated `graphs/youtube/helpers.py:_resolve_channel_ids` (`L4-21`).
Kept inline here per CODE-CONVENTIONS pragmatism: it's used only by
this node and ~15 LOC of Cypher.

Direct port of deprecated `graphs/youtube/adaptive.py:L94-133`."""
from __future__ import annotations

from ...state import AdaptiveRAGState
from .prompts import CLASSIFY_PROMPT
from .schemas import QueryClassification


def _resolve_channel_ids(neo4j_graph, channel_names: list[str]) -> list[str]:
    """Resolve channel/person names → channel ids via Neo4j.
    Searches both `Channel.name` and `Channel.id` (case-insensitive).
    Returns [] on any Cypher error so the caller falls back to "all
    channels" rather than crashing the classification."""
    if not channel_names:
        return []
    patterns = [n.lower() for n in channel_names]
    try:
        results = neo4j_graph.query(
            "MATCH (c:Channel) "
            "WHERE toLower(c.name) IN $names OR toLower(c.id) IN $names "
            "RETURN c.id AS channel_id",
            params = {"names": patterns},
        )
        return [r["channel_id"] for r in results if r.get("channel_id")]
    except Exception:
        return []


async def classify_query(
    state: AdaptiveRAGState, llm, neo4j_graph = None,
) -> dict:
    """Classify complexity + auto-detect channel scope.

    Routing precedence:
      1. `force_mode + channel_ids` already on state → skip LLM entirely
      2. LLM classifies; `force_mode` (if set) overrides the predicted
         `mode` but the LLM's `sub_questions` + `channel_names` are
         still used.
      3. Auto-resolve `channel_names` → `channel_ids` via Neo4j only
         when the caller didn't supply IDs."""
    channel_ids = state.get("channel_ids") or []
    force = state.get("force_mode")
    if force and channel_ids:
        return {
            "mode":          force,
            "sub_questions": [],
            "channel_ids":   channel_ids,
        }
    chain = CLASSIFY_PROMPT | llm.with_structured_output(
        QueryClassification, method = "function_calling",
    )
    try:
        result = await chain.ainvoke({"question": state["question"]})
        mode = force or result.mode
        sub_questions = result.sub_questions if mode == "deep" else []
        if not channel_ids and result.channel_names and neo4j_graph:
            channel_ids = _resolve_channel_ids(
                neo4j_graph, result.channel_names,
            )
        return {
            "mode":          mode,
            "sub_questions": sub_questions,
            "channel_ids":   channel_ids,
        }
    except Exception:
        return {
            "mode":          force or "standard",
            "sub_questions": [],
            "channel_ids":   channel_ids,
        }
