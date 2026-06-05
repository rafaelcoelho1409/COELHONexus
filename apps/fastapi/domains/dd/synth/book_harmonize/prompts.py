"""book_harmonize — LLM prompt templates (extract / canonicalize /
detect / patch) for the cross-chapter coherence pass."""
from __future__ import annotations


EXTRACT_CLAIMS_PROMPT = """Extract the atomic factual claims from this chapter of a distilled technical book.

Atomic claim = a single verifiable assertion about the technology (e.g., "library X
uses Y as its default serializer", "the timeout defaults to 30 seconds"). Cap at
{max_claims}. Skip motivational / structural / transitional sentences.

Also extract the chapter's key terminology — terms the chapter uses for specific
concepts. List them with their working definition AS USED IN THIS CHAPTER.

--- CHAPTER PROSE ---
{prose}
--- END PROSE ---

Return strict JSON:
{{
  "claims": ["claim 1", "claim 2", ...],
  "terms": [{{"name": "term as used", "definition": "1-sentence definition from chapter"}}]
}}"""


CANONICALIZE_PROMPT = """You are harmonizing terminology across the chapters of a distilled
technical book about {framework}. Below are the terms each chapter uses, with the
working definition the chapter applies.

For each TERM that appears across multiple chapters with DIFFERENT or CONFLICTING
definitions, decide the CANONICAL definition (or merge them if compatible). Skip
terms that are only used in one chapter or that have consistent definitions across
chapters.

--- PER-CHAPTER TERMINOLOGY ---
{terms_block}
--- END ---

Return strict JSON:
{{
  "canonical_terms": [
    {{"term": "name", "canonical_definition": "1-sentence canonical", "affected_chapters": ["ch_id1", "ch_id2"]}}
  ],
  "rationale": "1-sentence explanation of the harmonization choices made"
}}

If no canonicalization is needed, return {{"canonical_terms": [], "rationale": "..."}}."""


DETECT_PROMPT = """You are auditing chapter {chapter_id} of a distilled technical book about
{framework} for cross-chapter consistency issues.

Inspect for THREE classes of violations:
  1. CONTRADICTION — a claim in this chapter directly contradicts a claim in a sibling chapter
  2. DEFINITION_DRIFT — this chapter uses a term differently than the canonical definition
  3. TERMINOLOGY_DIVERGENCE — this chapter uses one name for a concept that sibling chapters call something else

--- THIS CHAPTER'S PROSE (truncated) ---
{this_prose}
--- END ---

--- CANONICAL TERMINOLOGY BANK ---
{canonical_terms}
--- END ---

--- ATOMIC CLAIMS FROM SIBLING CHAPTERS (sample) ---
{sibling_claims}
--- END ---

Return strict JSON:
{{
  "has_violations": true | false,
  "violations": [
    {{"kind": "contradiction" | "definition_drift" | "terminology_divergence",
      "this_chapter_says": "short quote or paraphrase",
      "should_say": "the canonical or sibling-chapter version",
      "evidence": "short pointer to where in this chapter"}}
  ],
  "summary": "1-sentence overall verdict"
}}

If no violations found, return {{"has_violations": false, "violations": [], "summary": "..."}}."""


PATCH_PROMPT = """You are minimally rewriting chapter {chapter_id} of a distilled technical book
about {framework} to resolve cross-chapter consistency violations. Preserve EVERYTHING
that isn't violating — same structure, same headings, same code references, same
citations, same tone.

ONLY change the spots flagged below. Use minimal edits — replace conflicting
definitions with canonical ones, swap divergent terms, fix contradictions.

VIOLATIONS TO FIX:
{violations_block}

CANONICAL TERMINOLOGY (use these definitions/names):
{canonical_terms}

--- ORIGINAL CHAPTER (REWRITE THIS, KEEP MARKDOWN STRUCTURE INTACT) ---
{original_prose}
--- END ---

Output: the full chapter prose, minimally edited. NO commentary, NO explanation,
NO JSON wrapping — output ONLY the markdown."""
