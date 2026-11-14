import type { RunStage } from "../params";

export type NodeResources = {
  agents: readonly string[];
  skills: readonly string[];
  tools: readonly string[];
};

const resources = (
  agents: readonly string[] = [],
  skills: readonly string[] = [],
  tools: readonly string[] = [],
): NodeResources => ({ agents, skills, tools });

const PLANNER_RESOURCES: Readonly<Record<string, NodeResources>> = {
  corpus_load: resources(),
  off_topic: resources(["page_judge"]),
  doc_distill: resources(["page_distiller"]),
  page_cards: resources(["page_card"]),
  chapter_design: resources(["chapter_designer"], ["chapter_designer"]),
  chapter_propose: resources(
    ["section_analyst", "chapter_architect", "plan_critic"],
    ["section_analyst", "chapter_architect", "plan_critic"],
    ["score_outline"],
  ),
  chapter_assign: resources(["page_scorer"]),
  chapter_select: resources(
    ["chapter_curator"],
    ["chapter_curator"],
    ["cluster_units", "validate_split"],
  ),
  order_chapters: resources(["chapter_sequencer"], ["chapter_sequencer"]),
  plan_write: resources(),
};

const SYNTH_RESOURCES: Readonly<Record<string, NodeResources>> = {
  outline_sdp: resources(["chapter_outliner"], ["chapter_outliner"], ["validate_outline"]),
  ledger_prepare: resources(),
  digest_construct: resources(["source_router"], [], ["check_digest"]),
  sawc_write: resources(
    ["section_writer", "coverage_extender", "source_extender"],
    [],
    ["check_section"],
  ),
  sawc_derive: resources(
    ["code_deriver", "capstone_designer"],
    ["capstone_designer"],
    ["check_code"],
  ),
  verify: resources(),
  checklist_eval: resources(["chapter_judge"]),
  mgsr_replan: resources(["chapter_reviewer"], ["chapter_reviewer"], ["inspect_section"]),
  render_audit_write: resources(["code_normalizer"], [], ["check_code"]),
};

/** Agent, skill, and custom tool names attached to each compiled pipeline node. */
export function nodeResources(
  stage: RunStage,
  nodeId: string,
  implemented: readonly string[] = [],
): NodeResources {
  const node = (stage === "planner" ? PLANNER_RESOURCES : SYNTH_RESOURCES)[nodeId] ?? resources();
  if (stage === "synth" && nodeId === "outline_sdp" && implemented.includes("ledger_prepare")) {
    return {
      ...node,
      tools: [...node.tools, "list_variants", "get_block", "compare_variants", "search_code"],
    };
  }
  return node;
}
