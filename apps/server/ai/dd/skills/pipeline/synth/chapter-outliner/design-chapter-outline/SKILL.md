---
name: design-chapter-outline
description: Decomposes one chapter of a framework's study book into a small set of sharply distinct sections with explicit prerequisites, checking the outline with the validate_outline tool before answering. Use for every chapter-outliner run, on the first attempt and on every retry with feedback.
compatibility: Needs the validate_outline tool and the read-only /sources/ files. Writes nothing.
metadata:
  version: "1.1"
  last-reviewed: "2026-11-10"
---

# Design a chapter outline

An outline is good when each section can be written from its own sources, shows code the other sections do not, and a reader who follows the prerequisites meets every idea after the ideas it uses. Check that with the tool; do not trust how the outline reads.

## 1. Read the index first

The request lists every source page with its headings, its code-block count and the start of its text. Group pages by the capability they teach. Open `/sources/<n>-<name>.md` only for a page whose headings do not say what it teaches, or to find the one distinct API a pair of near-duplicate sections would differ by.

## 2. Size it

Aim for `target_sections`, which is already sized to the number of sources and code blocks. A chapter with many code blocks needs enough sections to print them: about one section per 12 blocks. A chapter with few sources needs few sections; do not pad.

## 3. Name and describe

- Heading: the ONE central topic in 2 to 8 words, concrete and code-flavoured ("Async Client", "Tool Calling"), never a category ("Core Concepts", "Common Patterns") and never several topics glued with commas.
- Description: what the section teaches and which APIs it uses, in 20 to 400 chars. This text routes source pages to the section, so it must be specific enough to tell it apart from every other section.

## 4. Wire prerequisites

Only structural dependencies ("s3 uses the lifecycle defined in s1"), never merely thematic ones, and only to earlier sections. Prefer several shallow branches to one long spine: a chain deeper than 4 stages linearizes the chapter and removes the parallelism the writer relies on.

## 5. Validate, then answer

Call `validate_outline` with every section. It reports structural defects and pairs of sections whose scope overlaps (same APIs, same examples under different wording). Merge an overlapping pair under one heading, or re-scope one of them to a capability the other does not have. Fix every issue and call the tool again; answer when it says the outline is valid.

## 6. Tutorial chapters

When the request has `tutorial`, the outline is the plan of a complete tutorial: the reader learns every best code example and ends with an application that combines them.

- Start from `tutorial.best_code`, not from the page list. Group the examples into steps by what they build on: an example that defines something another example uses comes first.
- Every canonical example belongs to exactly one step. Put related examples for one learner capability in the same section when they fit a focused code bank; do not create one section per example. Put the APIs and defined names in `description`, so the router sends each example to its section.
- Order: setup and the smallest working example first, then one capability per step, each using only what earlier steps taught. Prerequisites follow that order.
- Use `list_variants` when a family has several variants and you need to know what the others add; `compare_variants` shows the difference. The canonical variant is the one to teach.
- A page that adds no example the others lack does not need a step of its own.

## Boundary examples

- "Session Management" and "Remote Control" that both cover persist, resume and in-memory stores are ONE section.
- A heading like "Extending the Agent with Skills, Hooks, System Prompts and Project Context" is four sections' worth: pick the central topic and split the rest out.
- A section id like `config-hierarchy` is rejected; the id is `s4` and the topic goes in the heading.
