"""Prompt templates: extract/canonicalize/detect/patch for cross-chapter coherence pass."""
from __future__ import annotations
from infra.langfuse.prompts import with_langfuse_override


_EXTRACT_CLAIMS_PROMPT = """Extract the atomic factual claims from this chapter of a distilled technical book.

Atomic claim = a single verifiable assertion about the technology (e.g., "library X
uses Y as its default serializer", "the timeout defaults to 30 seconds"). Cap at
{max_claims}. Skip motivational / structural / transitional sentences.

Also extract the chapter's key terminology — terms the chapter uses for specific
concepts. List them with their working definition AS USED IN THIS CHAPTER.

--- CHAPTER PROSE ---
{prose}
--- END PROSE ---

Output ONLY the JSON object below — no preamble, no restating these
instructions, no reasoning or explanation before or after it. Your
entire response must be the JSON object and nothing else.

Return strict JSON:
{{
  "claims": ["claim 1", "claim 2", ...],
  "terms": [{{"name": "term as used", "definition": "1-sentence definition from chapter"}}]
}}"""


@with_langfuse_override("dd.synth.book_harmonize.extract_claims")
def build_extract_claims_prompt(
    *,
    max_claims: str,
    prose: str,
) -> str:
    return _EXTRACT_CLAIMS_PROMPT.format(
        max_claims = max_claims,
        prose = prose,
    )


_CANONICALIZE_PROMPT = """You are harmonizing terminology across the chapters of a distilled
technical book about {framework}. Below are the terms each chapter uses, with the
working definition the chapter applies.

For each TERM that appears across multiple chapters with DIFFERENT or CONFLICTING
definitions, decide the CANONICAL definition (or merge them if compatible). Skip
terms that are only used in one chapter or that have consistent definitions across
chapters.

--- PER-CHAPTER TERMINOLOGY ---
{terms_block}
--- END ---

Output ONLY the JSON object below — no preamble, no restating these
instructions, no reasoning or explanation before or after it.

Return strict JSON:
{{
  "canonical_terms": [
    {{"term": "name", "canonical_definition": "1-sentence canonical", "affected_chapters": ["ch_id1", "ch_id2"]}}
  ],
  "rationale": "1-sentence explanation of the harmonization choices made"
}}

If no canonicalization is needed, return {{"canonical_terms": [], "rationale": "..."}}."""


@with_langfuse_override("dd.synth.book_harmonize.canonicalize")
def build_canonicalize_prompt(
    *,
    framework: str,
    terms_block: str,
) -> str:
    return _CANONICALIZE_PROMPT.format(
        framework = framework,
        terms_block = terms_block,
    )


_DETECT_PROMPT = """You are auditing chapter {chapter_id} of a distilled technical book about
{framework} for cross-chapter consistency issues.

Inspect for THREE classes of violations:
  1. CONTRADICTION — a claim in this chapter CANNOT be true together with a claim in a sibling chapter
  2. DEFINITION_DRIFT — this chapter defines a term from the canonical terminology bank differently from its canonical definition
  3. TERMINOLOGY_DIVERGENCE — this chapter uses one name for a concept that the canonical terminology bank or the sibling chapters call something else

Report ONLY conflicts with the sibling claims and the canonical terminology listed below. Do NOT correct this chapter from
anything you know outside those lists: the chapter is written from the documentation, and a statement the documentation
makes is not an error. A difference in level of detail, scope or emphasis is NOT a contradiction — both statements can be true.
When in doubt, report nothing.

--- THIS CHAPTER'S PROSE (truncated) ---
{this_prose}
--- END ---

--- CANONICAL TERMINOLOGY BANK ---
{canonical_terms}
--- END ---

--- ATOMIC CLAIMS FROM SIBLING CHAPTERS (sample) ---
{sibling_claims}
--- END ---

Output ONLY the JSON object below — no preamble, no restating these
instructions, no reasoning or explanation before or after it.

Return strict JSON:
{{
  "has_violations": true | false,
  "violations": [
    {{"kind": "contradiction" | "definition_drift" | "terminology_divergence",
      "this_chapter_says": "EXACT words copied from this chapter's prose (a verbatim phrase or sentence)",
      "should_say": "the canonical or sibling-chapter version",
      "sibling_chapter": "the chapter id shown in brackets in the sibling claims above (empty for a canonical-term issue)",
      "sibling_claim": "the sibling claim, or the canonical term, this conflicts with — copied from the lists above"}}
  ],
  "summary": "1-sentence overall verdict"
}}

If no violations found, return {{"has_violations": false, "violations": [], "summary": "..."}}."""


_PATCH_PARAGRAPH_PROMPT = """You are making a minimal edit to ONE paragraph of chapter {chapter_id} of a distilled technical
book about {framework}, to resolve the cross-chapter consistency violations flagged below.

Change only the flagged wording. Keep every other word, identifier, file name and number exactly as written; add no new
facts and no new sentences beyond what the fix needs.

VIOLATIONS TO FIX:
{violations_block}

CANONICAL TERMINOLOGY (use these definitions/names):
{canonical_terms}

--- PARAGRAPH ---
{paragraph}
--- END ---

Output: the edited paragraph only. NO commentary, NO quotes around it, NO markdown fences."""


@with_langfuse_override("dd.synth.book_harmonize.patch-paragraph")
def build_patch_paragraph_prompt(
    *,
    canonical_terms: str,
    chapter_id: str,
    framework: str,
    paragraph: str,
    violations_block: str,
) -> str:
    return _PATCH_PARAGRAPH_PROMPT.format(
        canonical_terms = canonical_terms,
        chapter_id = chapter_id,
        framework = framework,
        paragraph = paragraph,
        violations_block = violations_block,
    )


@with_langfuse_override("dd.synth.book_harmonize.detect")
def build_detect_prompt(
    *,
    canonical_terms: str,
    chapter_id: str,
    framework: str,
    sibling_claims: str,
    this_prose: str,
) -> str:
    return _DETECT_PROMPT.format(
        canonical_terms = canonical_terms,
        chapter_id = chapter_id,
        framework = framework,
        sibling_claims = sibling_claims,
        this_prose = this_prose,
    )


_PATCH_PROMPT = """You are minimally rewriting chapter {chapter_id} of a distilled technical book
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


@with_langfuse_override("dd.synth.book_harmonize.patch")
def build_patch_prompt(
    *,
    canonical_terms: str,
    chapter_id: str,
    framework: str,
    original_prose: str,
    violations_block: str,
) -> str:
    return _PATCH_PROMPT.format(
        canonical_terms = canonical_terms,
        chapter_id = chapter_id,
        framework = framework,
        original_prose = original_prose,
        violations_block = violations_block,
    )
