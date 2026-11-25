"""Orchestrate deterministic report formatting and PDF generation."""
from __future__ import annotations

import domains


async def render_pdf(
    report: domains.bd.distill.ai.schemas.StudyGuideDocument,
    source_figure_data: dict[str, bytes] | None = None,
) -> bytes:
    """Build the semantic document model, then compile its LaTeX source."""
    document = domains.bd.distill.source.rendering.latex.domain.build_document(report, source_figure_data)
    return await domains.bd.distill.source.rendering.latex.service.render_pdf(document)
