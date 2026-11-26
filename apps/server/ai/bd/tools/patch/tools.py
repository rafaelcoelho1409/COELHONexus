import domains
from . import schemas

from langchain.tools import ToolRuntime, tool


@tool
def check_patches(
    patches: list[schemas.PatchCheck],
    runtime: ToolRuntime[domains.bd.ingestion.ai.entities.SpecialistContext],
) -> str:
    """Validate all proposed patches together before returning the final batch.

    It checks assigned issue scope and exact source text. Every valid patch is automatically applied, regardless of
    risk, confidence, technical-content classification, or source-image verification. `issue_id` must be assigned to
    this specialist, and `target_text` must be copied exactly from the supplied Markdown.
    """
    results = []
    for number, patch in enumerate(patches, start = 1):
        outcome = runtime.context.check_patch(
            patch.issue_id,
            patch.risk,
            patch.target_text,
            patch.replacement,
            patch.confidence,
        )
        results.append(f"{number}. {patch.issue_id}: {outcome}")
    return "\n".join(results) or "no patches were given"
