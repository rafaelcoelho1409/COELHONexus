---
name: design-chapter-groups
description: Chooses a coherent chapter partition from measured page clusters, preserving best-code coverage and prerequisite order. Use for every Planner v2 chapter-design run.
---

# Design chapter groups

1. Read the candidate scores and inspect the cards in every group before choosing a partition.
2. Keep each tutorial chapter tied to one objective. Merge groups, including pages from different `.md` files, when their concepts and code build toward the same useful learner ability. Prefer a concise plan without collapsing distinct objectives.
3. Compare the available chapter-count alternatives and include the setup and navigation cost of each extra chapter. A count more than twice the soft estimate is a prompt to check for mergeable objectives, not a hard maximum; keep it only when source cards show genuinely distinct abilities or incompatible code paths. The minute estimate is a weak prior.
4. Put lookup-only API/reference groups in appendix chapters and mark them `synth_eligible=false` with a specific reason. Their source files remain attached to the plan for later lookup.
5. Preserve every unique best-code family and every distinct source-card teaching point in an eligible tutorial chapter. An appendix may contain best code or teaching points only when an eligible chapter also teaches the same material.
6. Declare capstone ability from the actual APIs shown in the cards. If examples do not compose, mark the chapter unable and explain the missing connection.
7. Add only prerequisite edges that a learner needs; use exact chapter titles and keep the graph acyclic.
8. Assign every group exactly once. Never optimize the chapter count by dropping pages or must-teach code.
