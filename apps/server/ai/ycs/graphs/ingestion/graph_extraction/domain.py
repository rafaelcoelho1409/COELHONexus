"""Pure evidence checks for candidate knowledge-graph facts."""
from __future__ import annotations

from .... import agents


def _normalized_excerpt(value: str) -> str:
    return " ".join(value.split()).casefold()


def validate_extraction(
    candidate: agents.schemas.GraphExtraction,
    transcript: str,
) -> tuple[agents.schemas.GraphExtraction, list[str]]:
    """Keep only candidates with transcript evidence and relations with valid endpoints."""
    source = _normalized_excerpt(transcript)
    issues: list[str] = []
    entities: list[agents.schemas.GraphEntityCandidate] = []
    entity_ids: dict[str, str] = {}
    for entity in candidate.entities:
        entity_id = entity.entity_id.strip()
        entity_type = entity.entity_type.strip()
        evidence = entity.evidence.strip()
        if not entity_id or not entity_type or not evidence or _normalized_excerpt(evidence) not in source:
            issues.append(f"Entity {entity.entity_id!r} lacks an exact transcript excerpt.")
            continue
        dedupe_key = entity_id.casefold()
        existing_type = entity_ids.get(dedupe_key)
        if existing_type is not None:
            if existing_type.casefold() != entity_type.casefold():
                issues.append(
                    f"Entity {entity_id!r} was assigned conflicting types "
                    f"{existing_type!r} and {entity_type!r}."
                )
            continue
        entity_ids[dedupe_key] = entity_type
        entities.append(entity.model_copy(update = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "evidence": evidence,
        }))

    ids_by_name = set(entity_ids)
    relationships: list[agents.schemas.GraphRelationshipCandidate] = []
    seen_relationships: set[tuple[str, str, str]] = set()
    for relation in candidate.relationships:
        source_id = relation.source_id.strip()
        target_id = relation.target_id.strip()
        relation_type = relation.relationship_type.strip()
        evidence = relation.evidence.strip()
        if (
            source_id.casefold() not in ids_by_name
            or target_id.casefold() not in ids_by_name
            or not relation_type
            or not evidence
            or _normalized_excerpt(evidence) not in source
        ):
            issues.append(
                f"Relationship {source_id!r} -> {target_id!r} has unsupported evidence or endpoints."
            )
            continue
        key = (source_id.casefold(), relation_type.casefold(), target_id.casefold())
        if key in seen_relationships:
            continue
        seen_relationships.add(key)
        relationships.append(relation.model_copy(update = {
            "source_id": source_id,
            "target_id": target_id,
            "relationship_type": relation_type,
            "evidence": evidence,
        }))
    return agents.schemas.GraphExtraction(entities = entities, relationships = relationships), issues
