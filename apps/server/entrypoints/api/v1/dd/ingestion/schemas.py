"""Response models for the finalized Docs Distiller ingestion library."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PageQualityProposal(BaseModel):
    target_text: str
    replacement: str
    rationale: str
    confidence: float
    risk: str


class PageQualityIssue(BaseModel):
    issue_id: str
    code: str
    severity: str
    description: str
    evidence: list[str]
    resolution: Literal["auto_applied", "review", "none"]
    proposal: PageQualityProposal | None = None


class PageQuality(BaseModel):
    """What Agentic AI found on a documentation page and how it was handled."""

    framework_slug: str
    agentic_run_id: str
    page: int
    page_kind: str
    status: Literal["done", "review_required"]
    delivery_disposition: Literal["include"] = "include"
    issues: list[PageQualityIssue]
    mechanical_fixes: dict[str, int] = Field(default_factory=dict)
    audit_warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
