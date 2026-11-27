"""Expected structured-output failures from evidence verification."""
from __future__ import annotations


class EvidenceReviewOutputError(ValueError):
    """The verifier completed without a response matching the EvidenceReview contract."""
