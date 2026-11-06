---
name: design-chapter-outline
description: Builds the final chapter outline of a framework's documentation by merging section sketches, checking it against the whole corpus with the score_outline tool, and having plan_critic review it from two lenses before answering. Use for every chapter-architect run, on the first attempt and on every retry with feedback.
compatibility: Needs the score_outline tool, the read-only /corpus/ files and the plan_critic subagent. Writes nothing.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-05"
---

# Design a chapter outline

An outline is good when a reader who follows it covers the whole documentation, one topic at a time, and no chapter is a dumping ground. Check that with the tools; do not trust how the outline reads.

## 1. Plan, then read

`write_todos` first: merge, score, review, answer. Read `/work/structure.md` (the documentation's own grouping) before `/work/sketches.json`. When there are no sketches, build the outline from `/corpus/` yourself: `grep` for the identifiers in the structure, `read_file` a page when its summary is ambiguous.

## 2. Merge the sketches

- Two sections that each propose a chapter on the same topic become ONE chapter; merge their concepts.
- A catalog chapter (one page per provider, plugin or loader) stays one chapter named after its section.
- Keep a chapter whole: do not move part of a page family into another chapter.
- Stay close to `target`. Under `min` or over `max` is a defect, not a style choice.

## 3. Let the numbers speak

`score_outline` reports (a) pages that match no chapter, (b) the largest chapter's share of the pages, (c) the chapter count against the target. After each change call it again, up to 3 times in all.

- Unmatched pages: `grep` their key terms. If they share a topic, add a chapter or widen a chapter's concepts; if they are overview or cross-cutting pages, leave them: the planner builds a Foundations chapter from them.
- A chapter over 30%: split it by its sub-themes, and check the parts are not each under 3 pages.
- Far from the target: merge the two closest chapters, or split the broadest one.

## 4. Review

Send two `task` calls to `plan_critic` in ONE message, one with the lens `coverage`, one with the lens `structure`, each carrying the whole outline. Treat their defects as claims to check: confirm one from the files before you act on it. Do not ask a third time.

## 5. Answer

Titles unique, concrete, 1 to 8 words; descriptions one sentence; concepts specific names that appear in the pages. See `references/rubric.md` for what separates a title worth keeping from a placeholder.

## Known failure modes

Leniency toward your own draft; merging two topics to hit the target; answering with a chapter per page family because the structure listed them; adding a chapter for the pages the score reports as unmatched without reading any of them.
