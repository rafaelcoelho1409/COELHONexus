import re


def _resolve_channel_ids(neo4j_graph, channel_names: list[str]) -> list[str]:
    """
    Resolve channel/person names to channel IDs via Neo4j.
    Searches both Channel.name and Channel.id (case-insensitive).
    """
    if not channel_names:
        return []
    patterns = [n.lower() for n in channel_names]
    try:
        results = neo4j_graph.query(
            "MATCH (c:Channel) "
            "WHERE toLower(c.name) IN $names OR toLower(c.id) IN $names "
            "RETURN c.id AS channel_id",
            params={"names": patterns},
        )
        return [r["channel_id"] for r in results if r.get("channel_id")]
    except Exception:
        return []


def _strip_think_tags(text: str) -> str:
    """Strip <think>...</think> reasoning tokens from model output."""
    return re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()