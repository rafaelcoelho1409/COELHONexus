---
name: review-failed-chapter
description: Decides which sections of a chapter that failed its checklist should be rewritten and what each rewrite must fix, reading the failing sections with the inspect_section tool first. Use for every chapter-reviewer run, after a checklist evaluation below the pass threshold.
compatibility: Needs the inspect_section tool. Reads only the JSON in the request.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-06"
---

# Review a failed chapter

A rewrite costs a full model run per section and can make a section worse. Rewrite only a section that shows a defect you have SEEN, and say what the defect is.

## 1. Map each failure to its sections

- A failure that names a section id or a heading points at that section.
- `chapter_reads_coherently`, `terminology_consistent` and `prose_code_first_not_meta_framing` usually point at the sections whose intros or explanations show the symptom: open them.
- `claims_grounded_in_sources` points at the section whose citation the feedback quotes.
- Deterministic gate failures (`all_sections_present`, `no_placeholder_sections`, `unique_headings`, `all_sections_cite_at_least_1`, `density_within_bounds`, `code_density_appropriate`, `code_uniqueness_ratio`, `code_snippet_sanity`, `code_coverage`, `code_prose_alignment`) list what they found; match their text to the section index (a section with `issues`, no citations, few code blocks).

## 2. Read before you decide

Call `inspect_section` for each candidate. A section whose text does not show the reported symptom is not rewritten.

## 3. Write the instruction

One or two sentences, naming the failed criterion and the exact symptom: "s4 opens with 'In this chapter we will explore'; open with the API it teaches." Do not restate the writer's rules and do not prescribe wording.

## 4. When to halt

Set `halt` when the failures cannot be fixed by rewriting prose: a chapter-wide coverage gap (sources the sections never cite), a judge outage, or failures that point at no section. A halt keeps the best draft so far.

## Boundary examples

- Five sections open with "This section covers": revise those five, not the other two.
- `code_coverage` fails and every section is already at its subtopic cap: halt; more rewriting cannot print more blocks.
