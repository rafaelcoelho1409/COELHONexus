"""Pure evidence and reference validation for graph candidates."""
from __future__ import annotations

import re

from ai.bd.agents.rag.graph_extractor import schemas
import domains


_SPACE = re.compile(r"\s+")


def validate_extraction(
    extraction: schemas.GraphExtraction,
    source_evidence: list[str],
) -> tuple[schemas.GraphExtraction, list[str]]:
    """Retain only entities and relationships with exact source quotations and known endpoints."""
    def is_supported(quote: str) -> bool:
        return any(domains.bd.distill.domain.quote_is_supported(quote, item) for item in source_evidence)

    issues: list[str] = []
    accepted_entities = []
    entity_ids: set[str] = set()
    for entity in extraction.entities:
        entity_key = _SPACE.sub(" ", entity.entity_id.strip()).casefold()
        if entity_key in entity_ids:
            issues.append(f"Duplicate entity: {entity.entity_id}")
            continue
        if not is_supported(entity.evidence):
            issues.append(f"Unsupported entity evidence: {entity.entity_id}")
            continue
        entity_ids.add(entity_key)
        accepted_entities.append(entity)

    accepted_relations = []
    for relation in extraction.relationships:
        source_key = _SPACE.sub(" ", relation.source_id.strip()).casefold()
        target_key = _SPACE.sub(" ", relation.target_id.strip()).casefold()
        if source_key not in entity_ids or target_key not in entity_ids:
            issues.append(f"Unknown relationship endpoint: {relation.source_id} → {relation.target_id}")
            continue
        if not is_supported(relation.evidence):
            issues.append(f"Unsupported relationship evidence: {relation.source_id} → {relation.target_id}")
            continue
        accepted_relations.append(relation)

    accepted = schemas.GraphExtraction(entities = accepted_entities, relationships = accepted_relations)
    return accepted, issues
