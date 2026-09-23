"""checklist_eval — LLM templates for CoCoA alignment + atomic-claim grounding."""
from __future__ import annotations
import infra


_COCOA_EXPLAINER_PROMPT = """You are the Code Explainer (CoCoA stage 1).

For each code snippet below, write ONE concise behavioral abstraction (1
sentence, 12-30 words) describing WHAT the code does and which key
identifiers (function names, decorators, types, parameters) appear in it.
This abstraction is NOT user-facing prose — it's a structured spec the
judge will compare against the documentation explanation.

OUTPUT — strict JSON, exactly this shape:
{{
  "abstractions": [
    {{"id": "<the integer id from the input>",
      "spec": "<1-sentence behavioral abstraction naming key identifiers>"}},
    ...
  ]
}}

Cover EVERY input id. No prose outside JSON.

== CODE BLOCKS ==
{blocks_block}
== END CODE BLOCKS =="""


@infra.langfuse.prompts.with_langfuse_override("dd.synth.checklist.cocoa_explainer")
def build_cocoa_explainer_prompt(
    *,
    blocks_block: str,
) -> str:
    return _COCOA_EXPLAINER_PROMPT.format(
        blocks_block = blocks_block,
    )


_COCOA_JUDGE_PROMPT = """You are the Alignment Judge (CoCoA stage 2).

For each row below, decide if the documentation EXPLANATION faithfully
describes the BEHAVIORAL SPEC of its associated code block. Both fields
were produced from the same source code; the spec is a reliable
abstraction of what that code does.

Pass criteria:
  - The explanation names ≥1 identifier from the spec (function name,
    decorator, type, parameter) AND
  - The explanation's main claim is consistent with the spec — same
    function, same purpose, no APIs invented that aren't in the spec.

Fail criteria (any one is sufficient):
  - The explanation describes a DIFFERENT API than the spec covers.
  - The explanation mentions identifiers that aren't in the spec.
  - The explanation is generic filler with no code-anchored detail.

OUTPUT — strict JSON, exactly this shape:
{{
  "verdicts": [
    {{"id": "<the integer id>",
      "aligned": true | false,
      "reason": "<short string; required when aligned = false, optional otherwise>"}},
    ...
  ]
}}

Cover EVERY input id. No prose outside JSON. Be strict — when in doubt,
prefer FAIL with a specific reason naming the drift.

== PAIRS ==
{pairs_block}
== END PAIRS =="""


@infra.langfuse.prompts.with_langfuse_override("dd.synth.checklist.cocoa_judge")
def build_cocoa_judge_prompt(
    *,
    pairs_block: str,
) -> str:
    return _COCOA_JUDGE_PROMPT.format(
        pairs_block = pairs_block,
    )


_ATOMIC_CLAIM_EXTRACT_PROMPT = """Extract the atomic factual claims from this chapter prose.
An atomic claim is a single verifiable fact about the technology being documented.

Examples of valid claims:
  - "Library X uses Y as its default serialization format"
  - "The timeout parameter defaults to 30 seconds"
  - "Function foo returns a list of strings when called with bar = True"

NOT claims (skip these):
  - Generic motivation ("This makes the API easier to use")
  - Section transitions ("Now we will discuss...")
  - Structural statements ("This chapter covers three topics")

Return strict JSON. Cap at {max_claims} most-important claims.

--- CHAPTER PROSE (truncated to {prose_chars} chars) ---
{prose}
--- END PROSE ---

JSON: {{"claims": ["claim 1", "claim 2", ...]}}"""


@infra.langfuse.prompts.with_langfuse_override("dd.synth.checklist.atomic_claim_extract")
def build_atomic_claim_extract_prompt(
    *,
    max_claims: str,
    prose: str,
    prose_chars: str,
) -> str:
    return _ATOMIC_CLAIM_EXTRACT_PROMPT.format(
        max_claims = max_claims,
        prose = prose,
        prose_chars = prose_chars,
    )


_ATOMIC_CLAIM_JUDGE_PROMPT = """Is the atomic claim at the END faithful to the source documentation?

A claim is SUPPORTED when ANY of these hold:
  (a) the source explicitly states it; OR
  (b) the source DEMONSTRATES it via code, example, or signature
      (e.g. "the snippet shows how to create a Browser instance" is
      supported when the source contains `Browser()` being instantiated);
      OR
  (c) the source trivially implies it from its API surface or shown
      behavior.

A claim is NOT supported when:
  - the source is silent AND the claim adds APIs/behavior not visible
    anywhere in the source; OR
  - the source contradicts the claim; OR
  - the claim invents specifics (parameter names, return types, error
    classes) absent from the source's text AND code.

Be charitable: code-first documentation often states facts BY
demonstrating them. Don't fail claims that the source backs through
example.

Answer in strict JSON: {{"supported": true | false, "evidence": "short quote OR symbol from source if supported, else empty"}}

--- SOURCE DOCUMENTATION (excerpt) ---
{source}
--- END SOURCE ---

CLAIM: {claim}"""


@infra.langfuse.prompts.with_langfuse_override("dd.synth.checklist.atomic_claim_judge")
def build_atomic_claim_judge_prompt(
    *,
    claim: str,
    source: str,
) -> str:
    return _ATOMIC_CLAIM_JUDGE_PROMPT.format(
        claim = claim,
        source = source,
    )


CRITERION_BLOCKS: dict[str, str] = {
    "chapter_reads_coherently": (
        "[c8] chapter_reads_coherently\n"
        "  Reading sections in order, does the chapter flow as a single "
        "document with smooth transitions, OR as disjoint reference "
        "cards with abrupt scope shifts? PASS if it reads as one "
        "document; FAIL if multiple sections feel like standalone "
        "definitions with no connective tissue."
    ),
    "claims_grounded_in_sources": (
        "[c9] claims_grounded_in_sources\n"
        "  Spot-check 3-5 citations against the per-section grounding "
        "above. Does each cited source actually back the specific claim "
        "the section makes in prose nearby? PASS if claims align with "
        "the digest's key_facts; FAIL if any cited source is being "
        "stretched beyond what it supports."
    ),
    "terminology_consistent": (
        "[c10] terminology_consistent\n"
        "  Does the chapter use the SAME name for the SAME concept "
        "across sections (e.g., not switching between 'field' and "
        "'attribute' for the same Pydantic concept, or 'method' and "
        "'function' interchangeably for the same API)? PASS if "
        "terminology is stable; FAIL if you can point to ≥2 sections "
        "using different names for the same thing."
    ),
    "prose_code_first_not_meta_framing": (
        "[c11] prose_code_first_not_meta_framing\n"
        "  Is each section's prose dense + production-focused (concrete "
        "APIs, types, parameters, error modes), OR padded with meta-"
        "framing ('In this chapter we will...', 'In summary...', 'It "
        "is important to note that...')? PASS if prose is dense; FAIL "
        "if meta-framing eats >20% of any section's `intro` or any "
        "H3 subtopic's `explanation`."
    ),
    "code_refs_introduced_in_prose": (
        "[c12] code_refs_introduced_in_prose\n"
        "  In the v2 cookbook structure, each H3 subtopic emits "
        "`{subheading} → {explanation} → [code-block]`. Does each "
        "subtopic's explanation (1-2 sentences BEFORE the code) "
        "actually introduce that specific code block — naming the "
        "decorator/type/parameter the reader is about to see — OR is "
        "it generic prose that could precede ANY code block? PASS if "
        "explanations are tied to their specific code; FAIL if any "
        "explanation reads as filler.\n"
        "  NOTE: If a section has 0 subtopics (rare — usually a "
        "placeholder), this criterion FAILS for that section. The "
        "cookbook contract requires ≥3 subtopics per section."
    ),
}
