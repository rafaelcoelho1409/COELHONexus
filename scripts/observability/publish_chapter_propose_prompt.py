"""One-shot uploader — publish `dd.planner.chapter_propose` to LangFuse.

The template MIRRORS the local f-string in
`apps/fastapi/domains/dd/planner/nodes/chapter_propose/prompts.py`. Keep
them in sync: if the local builder changes, re-run this script to push
the new shape with a fresh label.

Variables (must match the `variables=` dict passed by `get_prompt`):

    framework        target_chapters    n_source_keys
    proposals_min    proposals_max
    title_min_words  title_max_words
    concepts_min     concepts_max
    headings_block   namespaces_block
    corpus_label     corpus_block

Run inside the FastAPI container:
    kubectl exec -n coelhonexus-dev <fastapi-pod> -c coelhonexus-fastapi -- \\
        python /app/scripts/observability/publish_chapter_propose_prompt.py
"""
from __future__ import annotations

import logging
import sys


logging.basicConfig(level = logging.INFO, format = "%(levelname)s %(name)s: %(message)s")


PROMPT_NAME = "dd.planner.chapter_propose"
PROMPT_LABELS = ["production"]


PROMPT_TEMPLATE = """You are the Chapter Planner for the {{framework}} documentation.

Your job: propose a balanced set of about {{target_chapters}} chapters (TARGET={{target_chapters}}, sized to this corpus of {{n_source_keys}} docs; stay close to it, hard range {{proposals_min}}-{{proposals_max}}) that COVER THE FULL SURFACE AREA of this framework. Too FEW chapters forces unrelated topics to share one over-broad chapter; aim for ~{{target_chapters}} so each chapter is a cohesive, single-topic unit. Each chapter must:
  - have a concrete, specific title ({{title_min_words}}-{{title_max_words}} words; no generic 'Introduction'/'Overview'/'Conclusion')
  - cover a DISTINCT topic from every other chapter
  - be backed by ≥3 docs from the corpus
  - list {{concepts_min}}-{{concepts_max}} specific concepts/identifiers/commands that belong in it

== STRUCTURAL SIGNALS extracted from the corpus ==
Top recurring headings (appear in ≥2 docs):
  {{headings_block}}
File-tree namespaces (likely top-level groupings):
  {{namespaces_block}}

== CORPUS — {{n_source_keys}} {{corpus_label}} ==
{{corpus_block}}
== END CORPUS ==

OUTPUT — STRICT JSON:
{
  "proposals": [
    {
      "title":        "Concrete Topic Name",
      "description":  "One sentence describing what readers learn here.",
      "key_concepts": ["concept1", "command-name", "TypeName", ...]
    },
    ...
  ]
}

HARD RULES:
1. Between {{proposals_min}} and {{proposals_max}} chapters.
2. Titles UNIQUE case-insensitively.
3. NEVER use generic content-type names ('Introduction', 'Conclusion', 'Overview', 'Getting Started', 'About', 'Background', 'References') as a chapter title.
4. PREFER chapters that correspond to structural signals above (a 'commands/plugin' namespace → likely a 'Plugin Management' chapter).
5. For CLI tools: ensure every TOP-LEVEL subcommand visible in the corpus has chapter coverage somewhere.
6. Avoid mega-chapters: if your draft has any chapter that would absorb >40% of the docs, SPLIT it.

Respond ONLY with valid JSON. No prose, no markdown wrap."""


def main() -> int:
    from infra.langfuse import get_client
    client = get_client()
    if client is None:
        print("[publish] LangFuse client unavailable — env vars set?", file = sys.stderr)
        return 1
    try:
        out = client.create_prompt(
            name   = PROMPT_NAME,
            prompt = PROMPT_TEMPLATE,
            labels = PROMPT_LABELS,
            type   = "text",
        )
        ver = getattr(out, "version", "?")
        print(f"[publish] pushed {PROMPT_NAME!r} version={ver} labels={PROMPT_LABELS}")
        return 0
    except Exception as e:
        print(
            f"[publish] create_prompt failed: {type(e).__name__}: {e}",
            file = sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
