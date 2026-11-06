# Rubric and examples

Read when a title, a merge or a split is not obvious. The examples come from planner runs on real documentation sets.

## Titles

| Keep | Replace |
|---|---|
| "Streams and Queues" | "Overview" |
| "Subprocesses" | "Miscellaneous" |
| "Plugin Management" | "Getting Started" |
| "SDK Reference" | "References" |

A one-word title is fine when it is the topic ("Futures", "Queues"). A title that would fit any framework is a placeholder.

## Merge or split

- Merge when two chapters share more than half of their concepts, or when each is backed by fewer than 3 pages.
- Split when one chapter holds more than 30% of the pages, or when removing any third of its pages leaves a chapter that still makes sense on its own.
- Never split a page family (pages under one parent page).
- A catalog section (12 provider pages) is one chapter. Splitting it by "cloud providers" and "local providers" scatters pages a reader looks up by name.

## Reading the score

| Report | Meaning | Action |
|---|---|---|
| `unmatched` high | pages no chapter's concepts or description touch | read two of them; extend a chapter, add one, or accept them as cross-cutting |
| `largest_share` over 0.30 | a mega-chapter | split by sub-theme |
| `n_chapters` far under target | topics are squeezed together | split the broadest chapter |
| `n_chapters` far over target | near-duplicates | merge the pair with the most shared concepts |

The score is computed from page summaries, so a chapter whose concepts are abstract ("best practices") will look empty even when it is right. Prefer concepts that are identifiers, commands and type names.

## Why two critic lenses

A single reviewer tends to agree with a plausible outline. A reviewer told to assume the outline loses pages finds different problems from one told to assume it is badly shaped, and the pair costs two calls. A defect counts only when the reviewer names the pages or chapters it concerns.
