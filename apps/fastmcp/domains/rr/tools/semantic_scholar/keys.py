"""Identifier registries for the Semantic Scholar tool — per
docs/CODE-CONVENTIONS.md §2 (tuple-of-strings name lists belong here).
"""
from __future__ import annotations


# The rich `fields` set requested on every /paper/search call. Picked to
# maximize radar signal:
#   - paperId / title / abstract / year / publicationDate — base record
#   - authors.name — nested; only the display name (not authorId)
#   - citationCount / influentialCitationCount / referenceCount — signal-score inputs
#   - tldr — S2's pre-generated 1-sentence summary; SAVES an LLM call in distillation
#   - externalIds — {DOI, ArXiv, PubMed, MAG, ...} for cross-source dedup with arxiv
#   - openAccessPdf — direct PDF link for the deep-read subagent
#   - venue / fieldsOfStudy — categorical features for the score's `vertical_fit`
#
# Deliberately NOT included:
#   - embedding.specter_v2 — 768 floats per paper; bloats the wire. Add in v2
#                            when we want to skip our own embedding step.
DEFAULT_FIELDS: tuple[str, ...] = (
    "paperId",
    "title",
    "abstract",
    "authors.name",
    "year",
    "publicationDate",
    "citationCount",
    "influentialCitationCount",
    "referenceCount",
    "venue",
    "fieldsOfStudy",
    "openAccessPdf",
    "tldr",
    "externalIds",
)


# S2's controlled vocabulary for `fieldsOfStudy`. Used as a guide in the input
# schema description — we do NOT strictly validate against it (LLMs sometimes
# pass synonyms; S2 quietly ignores unknown values).
# Source: https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data
FIELDS_OF_STUDY: tuple[str, ...] = (
    "Computer Science",
    "Medicine",
    "Chemistry",
    "Biology",
    "Materials Science",
    "Physics",
    "Geology",
    "Psychology",
    "Art",
    "History",
    "Geography",
    "Sociology",
    "Business",
    "Political Science",
    "Economics",
    "Philosophy",
    "Mathematics",
    "Engineering",
    "Environmental Science",
    "Agricultural and Food Sciences",
    "Education",
    "Law",
    "Linguistics",
)
