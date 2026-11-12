from . import params

# Primary output field per node: how the web (and a resume) tells that a node has produced its result.
NODE_TO_FIELD = {
    "outline_sdp":        "outline_path",
    "digest_construct":   "digest_path",
    "sawc_write":         "sawc_path",
    "sawc_derive":        "derive_stats",
    "checklist_eval":     "checklist_path",
    "mgsr_replan":        "mgsr_path",
    "render_audit_write": "chapter_path",
}


def latest_key(slug: str, chapter_id: str, artifact: str) -> str:
    """Version-isolated pointer keys; v1 keeps every established object key byte-for-byte."""
    suffix = "-v2" if params.is_v2() else ""
    return f"dd/{slug}/synth/{chapter_id}/{artifact}{suffix}-latest.json"
