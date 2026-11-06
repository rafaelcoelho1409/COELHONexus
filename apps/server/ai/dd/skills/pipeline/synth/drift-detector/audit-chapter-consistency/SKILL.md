---
name: audit-chapter-consistency
description: Audits one chapter of a distilled book for contradictions, definition drift and terminology divergence against the canonical terminology and the claims of its sibling chapters, verifying a suspected conflict in the sibling chapters' text with grep before reporting it. Use for every drift-detector run.
compatibility: Needs the read-only /chapters/ files. Writes nothing.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-06"
---

# Audit a chapter for cross-chapter consistency

A false alarm edits a correct paragraph, so a violation is reported only when it is verified in the text of both chapters.

## 1. Collect candidates

Compare the chapter's prose with `canonical_terms` and `sibling_claims`. A candidate is a place where this chapter names a canonical concept differently, defines a canonical term differently, or asserts something a sibling claim denies.

## 2. Verify before you report

- Terminology: `grep` the sibling chapters for the name this chapter uses. If the siblings use it too, there is no divergence.
- Contradiction: open the sibling chapter at the claim (`grep` a distinctive word of it) and read the sentence around it. Both statements must be about the same thing under the same conditions.
- Definition drift: the chapter's wording must differ in meaning from the canonical definition, not merely in length.

## 3. What is NOT a violation

- A different level of detail, scope or emphasis.
- Something the documentation states that you know to be outdated or different elsewhere: the chapters are written from the documentation.
- A claim about a different version, platform or mode than the sibling's.

## 4. Report

`this_chapter_says` is copied verbatim from the chapter (at least 3 words) so the editor can find the paragraph. `should_say` is the canonical or sibling wording. When you verified nothing, report nothing: `has_violations` false.
