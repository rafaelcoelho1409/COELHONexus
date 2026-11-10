---
name: design-chapter-groups
description: Chooses a coherent chapter partition from measured page clusters, preserving best-code coverage and prerequisite order. Use for every Planner v2 chapter-design run.
---

# Design chapter groups

1. Read the candidate scores and inspect the cards in every group before choosing a partition.
2. Keep each tutorial chapter tied to one objective. Merge groups, including pages from different `.md` files, when their concepts and code build toward the same useful learner ability. Prefer a concise plan without collapsing distinct objectives.
3. Compare the available chapter-count alternatives and include the setup and navigation cost of each extra chapter. Do not choose only by the smallest count; compare candidate scores, largest and smallest lesson sizes, very short lesson counts, objectives, and code paths. Merge a very short chapter into the closest compatible objective when that does not hide a distinct ability. Treat the suggested count as a soft anchor, not a target to hit mechanically. Every additional chapter should preserve a distinct learner ability or make a materially oversized lesson safer. A count more than twice the estimate is a strong prompt to check for mergeable objectives, not a hard maximum; keep it only when source cards show genuinely distinct abilities or incompatible code paths.
4. Put lookup-only API/reference groups in appendix chapters and mark them `synth_eligible=false` with a specific reason. Their source files remain attached to the plan for later lookup; unique provider names or option listings do not by themselves make a tutorial objective.
5. Preserve every unique best-code family and every distinct teaching point from a tutorial page in an eligible chapter. Keep a reference group eligible only when its pages teach a concrete conceptual or procedural ability needed in the study path.
6. Declare capstone ability from the actual APIs shown in the cards. If examples do not compose, mark the chapter unable and explain the missing connection.
7. Add only prerequisite edges that a learner needs; use exact chapter titles and keep the graph acyclic.
8. Set `candidate_partition_index` to the selected partition's zero-based index in `candidate_partitions`, then assign every globally unique short group-id alias from that partition exactly once. Numeric `page_ids` refer to the shared page evidence table. Echo each group alias exactly; never return internal group or partition ids. Every returned chapter must have a non-empty `group_ids` array. Never optimize the chapter count by dropping pages or must-teach code.
