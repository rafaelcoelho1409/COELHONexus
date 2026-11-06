---
name: review-chapter-outline
description: Reviews a draft chapter outline of a framework's documentation through one lens, coverage or structure, running the score_outline check first and grounding every defect in named pages or chapters. Use when asked to act as a critic or reviewer of a chapter outline before it is accepted.
compatibility: Needs the score_outline tool and the read-only /work/ and /corpus/ files. Writes nothing.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-05"
---

# Review a chapter outline

A critic checks evidence, not prose. A defect exists because a page, a count or a line of the structure shows it.

## 1. Run the mechanical check first

Call `score_outline` with the outline. It settles three things code can settle: pages that match no chapter, the largest chapter's share, the distance from the target. Do not spend judgement on them; read the report and decide what it implies.

## 2. Take ONE lens

- **coverage**: assume the outline loses pages. For each page `score_outline` calls unmatched, `grep` its key terms and open it: does it teach something no chapter backs? Then look for chapters backed by fewer than 3 pages (`grep` the chapter's concepts: how many pages hit?).
- **structure**: assume the outline is badly shaped. Compare chapter against chapter: do two share most of their concepts? Does one mix unrelated sub-themes? Does a chapter hold more than 30% of the pages? Is a catalog of same-kind pages split across chapters? Does a chapter cut a page family from `/work/structure.md`?

## 3. Report defects with evidence

`chapter or pages | what is wrong | the fix`, one line each. A defect with no page or chapter named is dropped. Do not report style preferences, title taste or an ordering you would change.

## 4. Verdict

`ACCEPT` when no defect survives, `REVISE` otherwise. When unsure whether something is a defect, report it as one line prefixed `unverified:` and do not count it toward REVISE.

## Known failure modes

Leniency (accepting what reads well), restating the architect's reasoning as your own, and reporting a defect the score already explains away (overview pages are meant to be unmatched).
