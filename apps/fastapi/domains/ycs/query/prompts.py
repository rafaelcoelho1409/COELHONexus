"""ycs/query — LLM prompts for the text-to-DSL AI field.

Per the Neo4j Text2Cypher Guide (Feb 2026) + the Text-to-ES Bench
paper (ACL 2025): schema-grounded few-shot prompting is the proven
baseline. The prompt builder below assembles three components:

  1. A short rules block (read-only, response format, no commentary).
  2. A compact schema slice (the user-visible store schema; trimmed if
     it would blow the context budget).
  3. 3–5 curated few-shot exemplars (from examples.py).

Repair prompt (`build_repair_prompt`) is the same shell with the
previous attempt + parser error appended — the model gets ONE shot at
fixing its own output before we surface the error to the user.
"""
from __future__ import annotations

import json
from typing import Any

from .params import BACKEND_ES, BACKEND_NEO4J, BACKEND_QDRANT


PROMPT_VERSION = "qaie-v1.3.0"   # Qdrant filter-shape rules + text-index hint
_SCHEMA_CHARS_CAP = 9000


_RULES = {
    BACKEND_ES: (
        "You are a senior Elasticsearch engineer.\n"
        "Generate ONE Elasticsearch _search request body that answers the user's question.\n"
        "Hard constraints:\n"
        " · OUTPUT MUST BE A SINGLE JSON OBJECT — no markdown, no prose, no commentary.\n"
        " · Read-only — never produce `script`, `delete_by_query`, `update_by_query`.\n"
        " · Always set `size` (cap at 200).\n"
        " · Query the YCS metadata + transcriptions indexes (the server pins the path).\n"
        " · GROUND IN THE SCHEMA BELOW — only use field names that appear under `# index: …`.\n"
        " · USE REAL VALUES — if a `# top values` line lists actual values for a keyword field,\n"
        "   pick from that list rather than inventing placeholders like \"REPLACE_ME\".\n"
        " · If the user names a field that doesn't exist in the schema, pick the closest match\n"
        "   that DOES exist (don't fabricate field names).\n"
    ),
    BACKEND_QDRANT: (
        "You are a senior Qdrant engineer.\n"
        "Generate ONE Qdrant query body that answers the user's question.\n"
        "Hard constraints:\n"
        " · OUTPUT MUST BE A SINGLE JSON OBJECT — no markdown, no prose, no commentary.\n"
        " · Always include `\"op\"` discriminator — one of: \"search\", \"scroll\", \"query_points\", \"count\".\n"
        " · Read-only — never use upsert / delete / update ops.\n"
        " · For `search` you typically don't have a query vector at hand; prefer `scroll` with a payload filter.\n"
        " · `limit` must be present and <= 200.\n"
        " · GROUND IN THE SCHEMA BELOW — use payload keys that appear under `# observed payload keys` or\n"
        "   `# sample payload` blocks. Pull example values from the sample payloads.\n"
        " · VALID FILTER SHAPE — `scroll_filter` (or `filter`) is `{must|should|must_not: [<clause>...]}`.\n"
        "   Each clause is `{\"key\": \"<field>\", \"match\": {<one_of>}}`. The `match` block has EXACTLY one of:\n"
        "     - `{\"value\": <scalar>}`   — exact keyword/integer match (works on keyword-indexed fields)\n"
        "     - `{\"any\": [<scalars>]}`  — match any of a list\n"
        "     - `{\"except\": [<scalars>]}` — match none of a list\n"
        "     - `{\"text\": \"<substring>\"}` — full-text contains (ONLY on text-indexed fields)\n"
        "   `match_text` is NOT a Qdrant operator — never emit it. Range filters use\n"
        "   `{\"key\": \"<field>\", \"range\": {\"gt\"|\"gte\"|\"lt\"|\"lte\": <number>}}` (no `match` wrapper).\n"
        " · NEVER use `match: {text: ...}` on a field that isn't listed under `# text-indexed fields`.\n"
        "   If the user wants substring search on `content` / `title` / any other un-indexed text field,\n"
        "   return a plain `scroll` (no filter) — Qdrant cannot do substring search on unindexed fields,\n"
        "   so the user should switch to the Elasticsearch backend for full-text queries.\n"
    ),
    BACKEND_NEO4J: (
        "You are a senior Neo4j engineer.\n"
        "Generate ONE Cypher query that answers the user's question.\n"
        "Hard constraints:\n"
        " · OUTPUT MUST BE A SINGLE CYPHER STATEMENT — no markdown, no prose, no commentary.\n"
        " · Read-only — never use CREATE, MERGE, DELETE, SET, REMOVE, or any write procedure.\n"
        " · Always include a LIMIT clause (default 25).\n"
        " · GROUND IN THE SCHEMA BELOW — only use labels listed under `# labels`, relationship\n"
        "   types listed under `# relationship_types`, and properties listed under each `(Label):`.\n"
        " · USE ONLY RELATIONSHIPS THAT EXIST — pick traversal patterns from the\n"
        "   `# observed relationships` block. If the user asks about a connection that's NOT in\n"
        "   that list, return a MATCH that COULDN'T match (e.g. `WHERE false`) rather than\n"
        "   inventing a relationship type.\n"
        " · USE REAL PROPERTY VALUES — pull example IDs / names / titles from the `# sample nodes`\n"
        "   block rather than inventing placeholders.\n"
    ),
}


def _condense_schema(backend: str, schema: dict[str, Any] | None) -> str:
    """Produce a compact human-readable schema slice for the prompt.

    Renders THREE layers per backend:
      1. Declared schema  — field names + types (cheap, always cheap)
      2. Real value samples — actual values pulled from the live store
      3. Observed shape   — for Neo4j: real (src)-[REL]->(dst) triples;
                             for ES/Qdrant: extra payload keys + sample docs.

    These extra layers are what flip the model from "plausible-looking
    generic query" to "query that actually fits THIS database" — the
    finding the user surfaced 2026-06-16.

    `_SCHEMA_CHARS_CAP` is the hard cap; content is truncated with an
    ellipsis after so we never blow the context budget."""
    if not schema:
        return "(schema unavailable; rely on common YCS field names: title, channel_id, content, video_id, webpage_url, upload_date)"

    if backend == BACKEND_ES:
        lines: list[str] = []
        for name, idx in schema.get("indices", {}).items():
            lines.append(f"# index: {name} ({idx.get('doc_count', '?')} docs)")
            props = idx.get("mappings", {}).get("properties", {}) or {}
            for fname, fcfg in props.items():
                t = fcfg.get("type") or "object"
                lines.append(f"  {fname}: {t}")
            # Real top values per keyword field — gives the LLM ground
            # truth for things like `channel_id`, `lang`, `playlist_id`.
            field_values = idx.get("field_values") or {}
            if field_values:
                lines.append(f"# top values in {name}:")
                for fname, vals in field_values.items():
                    sample = ", ".join(repr(v)[:40] for v in vals[:5])
                    lines.append(f"  {fname} ∈ [{sample}]")
            # Sample docs — small, helps with field-population
            # heuristics (which fields are populated, typical shapes).
            samples = idx.get("samples") or []
            for j, s in enumerate(samples, 1):
                lines.append(f"# sample {j} from {name} (_id={s.get('_id')!r}):")
                src = s.get("_source") or {}
                lines.append("  " + json.dumps(src, ensure_ascii = False)[:600])
        text = "\n".join(lines)

    elif backend == BACKEND_QDRANT:
        lines = []
        for c in schema.get("collections", []):
            lines.append(f"# collection: {c['name']} ({c.get('points_count', '?')} points)")
            payload = c.get("payload_schema", {}) or {}
            for fname, fcfg in payload.items():
                lines.append(f"  {fname}: {fcfg.get('data_type')} (indexed → filterable with `match`)")
            vc = c.get("vectors_config")
            if vc:
                lines.append(f"  __vectors__: {json.dumps(vc)[:200]}")
            observed = c.get("observed_payload_keys") or []
            if observed:
                lines.append("# observed payload keys: " + ", ".join(observed))
            # Text-index hint — critical for stopping the AI from
            # emitting `match: {text: ...}` on un-indexed string fields
            # (which 500s at the Qdrant Pydantic layer).
            text_idx = c.get("text_indexed_fields") or []
            if text_idx:
                lines.append(
                    "# text-indexed fields (`match: {text: ...}` allowed): "
                    + ", ".join(text_idx),
                )
            else:
                lines.append(
                    "# NO text-indexed fields — `match: {text: ...}` will FAIL "
                    "on this collection. For substring search on `content` / `title` / etc. "
                    "use a plain scroll (no filter) or switch to the Elasticsearch backend."
                )
            samples = c.get("samples") or []
            for j, s in enumerate(samples, 1):
                lines.append(f"# sample payload {j} (id={s.get('id')!r}):")
                lines.append("  " + json.dumps(s.get("payload", {}), ensure_ascii = False)[:600])
        text = "\n".join(lines)

    elif backend == BACKEND_NEO4J:
        lines = []
        lines.append("# labels: " + ", ".join(schema.get("labels") or []))
        lines.append("# relationship_types: " + ", ".join(schema.get("relationship_types") or []))
        node_props = schema.get("node_properties", {}) or {}
        for label, props in node_props.items():
            names = ", ".join(p["name"] for p in (props or []))
            lines.append(f"  ({label}): {names}")
        patterns = schema.get("relationship_patterns") or []
        if patterns:
            lines.append("# relationships (Cypher-shaped; × N = live observed count):")
            for p in patterns[:30]:
                c = p.get("count")
                if c is None:
                    # Declared by writer code but currently unpopulated.
                    suffix = "  (declared; 0 instances live)"
                else:
                    suffix = f"  × {c}"
                lines.append(
                    f"  (:{p['src']})-[:{p['rel']}]->(:{p['dst']}){suffix}"
                )
        node_samples = schema.get("node_samples") or {}
        if node_samples:
            lines.append("# sample nodes:")
            for label, samples in node_samples.items():
                for s in samples[:2]:
                    props = json.dumps(s.get("properties", {}), ensure_ascii = False)[:280]
                    lines.append(f"  (:{label}) " + props)
        text = "\n".join(lines)

    else:
        text = json.dumps(schema)[:_SCHEMA_CHARS_CAP]

    if len(text) > _SCHEMA_CHARS_CAP:
        text = text[:_SCHEMA_CHARS_CAP].rstrip() + "\n# …schema truncated"
    return text


def _format_examples(examples: list[dict[str, str]]) -> str:
    """Few-shot block — Q→A pairs interleaved. The Q line uses a
    distinctive prefix the model treats as an instruction header; the A
    block holds the raw DSL with no surrounding fence (we DON'T want
    the model to imitate markdown fences in its output)."""
    out: list[str] = []
    for ex in examples:
        out.append(f"Question: {ex['question']}\nQuery:\n{ex['query']}\n")
    return "\n".join(out)


def build_generate_prompt(
    *, backend: str, user_prompt: str, schema: dict[str, Any] | None,
    examples: list[dict[str, str]], previous: str = "",
) -> str:
    """Assemble the full prompt fed to `app.state.llm.ainvoke(...)`.

    The model's output is then taken VERBATIM as the query body — no
    parsing past the read-only safety guard."""
    rules     = _RULES.get(backend, _RULES[BACKEND_ES])
    schema_s  = _condense_schema(backend, schema)
    fewshot   = _format_examples(examples)
    prior     = (
        f"\nCurrent editor content (refine this if it's relevant):\n{previous.strip()}\n"
        if previous.strip() else ""
    )
    return (
        f"{rules}\n"
        f"--- SCHEMA ---\n{schema_s}\n\n"
        f"--- EXAMPLES ---\n{fewshot}\n"
        f"--- USER REQUEST ---\n{user_prompt.strip()}\n"
        f"{prior}\n"
        "--- OUTPUT ---\n"
        "Respond with the query body ONLY. No explanation. No markdown."
    )


def build_repair_prompt(
    *, backend: str, user_prompt: str, attempt: str, error: str,
    schema: dict[str, Any] | None, examples: list[dict[str, str]],
) -> str:
    """Self-repair shell — same context as generation + the previous
    attempt + the safety / parse error we want fixed.

    Matches the loop described in *Cypher Generation: The Good, The Bad
    and The Messy* (TDS, 2025)."""
    rules    = _RULES.get(backend, _RULES[BACKEND_ES])
    schema_s = _condense_schema(backend, schema)
    fewshot  = _format_examples(examples)
    return (
        f"{rules}\n"
        f"--- SCHEMA ---\n{schema_s}\n\n"
        f"--- EXAMPLES ---\n{fewshot}\n"
        f"--- USER REQUEST ---\n{user_prompt.strip()}\n\n"
        f"--- PREVIOUS ATTEMPT (rejected) ---\n{attempt.strip()}\n\n"
        f"--- REJECTION REASON ---\n{error.strip()}\n\n"
        "--- OUTPUT ---\n"
        "Respond with a CORRECTED query body ONLY. No explanation. No markdown."
    )
