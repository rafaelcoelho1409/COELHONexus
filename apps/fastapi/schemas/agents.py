from pydantic import BaseModel, Field


# =============================================================================
# Structured Output Models
# =============================================================================
class QueryClassification(BaseModel):
    """Output of the query classifier."""
    mode: str = Field(
        description = "Query mode: 'fast' for simple factual, 'standard' for evidence-based, 'deep' for analytical"
    )
    reasoning: str = Field(
        description = "Brief explanation of why this mode was chosen"
    )
    sub_questions: list[str] = Field(
        default_factory = list,
        description = "For 'deep' mode: 3-8 focused sub-questions to investigate"
    )
    channel_names: list[str] = Field(
        default_factory = list,
        description = "Channel or person names mentioned in the query (for scope filtering)"
    )


class CriticAssessment(BaseModel):
    """Output of the critic node."""
    confidence_score: float = Field(
        description = "Confidence in the synthesis quality (0.0-1.0)"
    )
    claims_supported: bool = Field(
        description = "True if all claims in the synthesis are supported by subagent evidence"
    )
    reasoning: str = Field(
        description = "Brief explanation of the assessment"
    )


class ResearchPlan(BaseModel):
    sub_questions: list[str] = Field(description = "3-8 focused sub-questions")
    strategy: str = Field(description = "Brief research strategy")


class HallucinationCheck(BaseModel):
    """Result of hallucination detection."""
    grounded: bool = Field(
        description = "True if ALL claims in the answer are supported by the source documents"
    )
    addresses_question: bool = Field(
        description = "True if the answer actually addresses the original question"
    )
    reason: str = Field(
        description = "Brief explanation of the assessment"
    )


class GradeResult(BaseModel):
    """Binary relevance grade for a document."""
    score: str = Field(
        description = "'relevant' if the document answers the question, 'not_relevant' otherwise"
    )


class SchemaDiscovery(BaseModel):
    """Auto-discovered knowledge-graph schema from sample transcripts."""
    allowed_nodes: list[str] = Field(
        description = "Entity types to extract (e.g., Country, Person, Organization)"
    )
    allowed_relationships: list[str] = Field(
        description = "Relationship types (e.g., RECOMMENDS, LOCATED_IN)"
    )
    extraction_focus: str = Field(
        description = "Brief description of what to focus on during extraction"
    )


class ExtractedEntities(BaseModel):
    """Entity names identified in a user query (for graph retrieval)."""
    entities: list[str] = Field(
        description = "List of entity names (people, topics, technologies, channels) mentioned in the query"
    )