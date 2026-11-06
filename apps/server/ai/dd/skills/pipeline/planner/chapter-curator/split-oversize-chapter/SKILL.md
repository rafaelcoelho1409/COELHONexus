---
name: split-oversize-chapter
description: Splits one oversize chapter of a framework's documentation plan into cohesive chapters over its page-family units, using cluster_units to group the units and validate_split to check the partition before answering. Use for every chapter that holds too large a share of the corpus or too many code blocks.
compatibility: Needs the cluster_units and validate_split tools. Reads only the JSON in the request.
metadata:
  version: "1.0"
  last-reviewed: "2026-11-05"
---

# Split an oversize chapter

A chapter is too big when a reader cannot hold it as one unit. The split must keep what belongs together together; sizes come second.

## 1. Let the tool group first

Call `cluster_units(n_parts)`. It groups the units by shared path directories, titles and summaries and returns each group with its units. Prefer its grouping: it is computed from the pages, not from how the titles read. When it says the units do not cluster, partition them yourself by topic.

## 2. Name the groups

For each group, read the titles and summaries of its units, then give the group:
- a title of 1 to 8 words that names what the group teaches (a feature, a component, a workflow), not its size;
- one sentence saying what the reader learns.

Titles must differ from every title in `other_titles` and from each other, case-insensitively. Never "Introduction", "Overview", "Miscellaneous", "Other" or "Part 2".

## 3. Validate, then answer

Call `validate_split` with the groups. Fix what it reports (a unit in two groups, a unit in none, a duplicate title, a group under the size floor) and call it again. Answer only after it says the split is valid.

## Boundary examples

- Units "Streams: reading", "Streams: writing", "Queues: basics", "Queues: priority" cluster into "Streams" and "Queues", not into "Basics" and "Advanced".
- A group of one unit is allowed only when that unit holds enough pages to meet the floor.
- When the tool returns three groups and you would rather have two, keep three: the tool's grouping is the evidence, your preference is not.
