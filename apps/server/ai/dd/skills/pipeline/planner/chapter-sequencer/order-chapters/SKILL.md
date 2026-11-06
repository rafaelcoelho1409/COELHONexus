---
name: order-chapters
description: Orders the chapters of a technical learning book into reading order by stages (orientation, core usage, extended features, production and operations, reference and maintenance), returning chapter titles exactly as given. Use for every chapter-sequencer sample.
compatibility: Reads only the JSON in the request; needs no tools.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-05"
---

# Order the chapters

A reader goes front to back: every chapter must be understandable from the ones before it.

## 1. Assign each chapter a stage

1. Orientation: what the framework is, installation, setup, quickstart, the mental model.
2. Core usage: the building blocks nearly every application uses.
3. Extended features: optional capabilities built on the core.
4. Production and operations: security and auth, deployment, observability, testing, performance, command-line tooling.
5. Reference and maintenance: API listings, migration and upgrade guides, changelogs, FAQ, miscellany.

## 2. Order inside a stage

- Concrete usage before abstract internals; simple before complex.
- A chapter that is mostly API reference (its `reference_pct` is high) comes after the guide chapters on the same area.
- Chapters that depend on another (an extension of a feature) come right after it.

## 3. Migration and upgrade chapters

They serve readers who already know an older version: stage 5, never first, even when they are short.

## 4. Check the answer

- Exactly `n_chapters` titles, each once, spelled as given. A title you shorten or reword is a title the planner cannot match.
- The rationale (2 to 3 sentences) names what a newcomer reads first and why; the order follows from it.
