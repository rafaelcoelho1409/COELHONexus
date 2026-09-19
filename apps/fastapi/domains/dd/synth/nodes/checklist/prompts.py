"""checklist_eval — LLM templates for CoCoA alignment + atomic-claim grounding."""
from __future__ import annotations


COCOA_EXPLAINER_PROMPT = """You are the Code Explainer (CoCoA stage 1).

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


COCOA_JUDGE_PROMPT = """You are the Alignment Judge (CoCoA stage 2).

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


ATOMIC_CLAIM_EXTRACT_PROMPT = """Extract the atomic factual claims from this chapter prose.
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


ATOMIC_CLAIM_JUDGE_PROMPT = """Is the atomic claim at the END faithful to the source documentation?

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
