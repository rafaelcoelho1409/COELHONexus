# Primary output field per node: how the web (and a resume) tells that a node has produced its result.
NODE_TO_FIELD = {
    "corpus_load":      "raw_files",
    "off_topic":        "relevant_files",
    "doc_distill":      "doc_distill_ref",
    "chapter_propose":  "chapter_proposals_ref",
    "chapter_assign":   "chapter_doc_assignments_ref",
    "chapter_select":   "chapter_plan_ref",
    "order_chapters":   "chapter_order_ref",
    "plan_write":       "plan_path",
}
