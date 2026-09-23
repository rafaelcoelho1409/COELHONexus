"""render — Jinja2 environment + inline chapter template."""
from __future__ import annotations
import infra

from jinja2 import Environment, StrictUndefined


# StrictUndefined: unset template vars crash loudly instead of emitting silent None literals.
JINJA_ENV = Environment(
    autoescape = False,
    undefined = StrictUndefined,
    keep_trailing_newline = True,
    lstrip_blocks = True,
    trim_blocks = True,
)


CHAPTER_MD_TEMPLATE = """\
# {{ chapter_title }}

{% if toc -%}
## Contents

{% for entry in toc -%}
- [{{ entry.heading }}](#{{ entry.anchor }})
{% for sub in entry.subtopics -%}
  - [{{ sub.subheading }}](#{{ sub.anchor }})
{% endfor -%}
{% endfor %}

---

{% endif -%}
{% for section in sections %}
## {{ section.heading }}

{% if section.intro -%}
{{ section.intro }}

{% endif -%}
{% for sub in section.subtopics %}
### {{ sub.subheading }}

{{ sub.explanation }}

{% if sub.derived_caption -%}
{{ sub.derived_caption }}
{% endif -%}
{{ sub.code_block }}

{% endfor -%}
{% if section.citations -%}
**Sources for this section:**

{% for c in section.citations -%}
- `{{ c.source_basename }}` — {{ c.claim }}
{% endfor %}

{% endif -%}
{% endfor %}
"""


_NORMALIZE_PROMPT_BASE = (
    "You are a code formatter. Fix indentation, line-break, and "
    "whitespace so the {lang} code below is correctly formatted. The "
    "code was likely mangled by upstream tooling that flattened "
    "indentation (Mintlify MDX export, HTML-rendered copy-paste, etc.) "
    "— EVERY continuation that's supposed to be nested often ends up at "
    "column 0.\n\n"
    "Look for and fix THESE common failure modes (assume they're "
    "present unless you can confirm otherwise):\n"
    "1. Function / class / if / for / while / try / with bodies sitting "
    "at the SAME column as their `def`/`class`/etc. header — must be "
    "MORE indented (Python: 4 spaces deeper). This is a hard syntax "
    "error.\n"
    "2. Keyword arguments inside a function CALL `foo(` ... `)` sitting "
    "at column 0 — must be indented one level deeper than the opening "
    "`(`. This parses but is unreadable and wrong style.\n"
    "3. Items inside `[...]` / `{{...}}` literals sitting at column 0 "
    "— same rule: indent one level deeper than the opening bracket.\n"
    "4. Method chains, conditional expressions, and `return` "
    "continuations broken across lines but flattened to column 0 — "
    "indent the continuation.\n\n"
    "Strict rules:\n"
    "- Preserve every identifier, string literal, number, operator, "
    "comment, and language keyword BYTE-EXACT. Only whitespace may "
    "change. NEVER rename, NEVER reorder, NEVER add or remove tokens.\n"
    "- Use 4-space indents for Python; match the original style for "
    "other languages.\n"
    "- If the code is genuinely already correct, return it unchanged.\n"
    "- Return ONLY the fixed code. NO fences, NO commentary, NO "
    "preamble, NO explanation.\n\n"
    "```{lang}\n{body}\n```"
)


@infra.langfuse.prompts.with_langfuse_override("dd.synth.render.normalize_base")
def build_normalize_base(
    *,
    body: str,
    lang: str,
) -> str:
    return _NORMALIZE_PROMPT_BASE.format(
        body = body,
        lang = lang,
    )

_NORMALIZE_PROMPT_PYTHON_RETRY = (
    "The Python code below failed to parse with `ast.parse` — likely "
    "because function or class bodies are at the same indent level as "
    "their `def`/`class` header (Mintlify MDX flattening). Fix the "
    "indentation so every `def`/`class`/`if`/`for`/`while`/`try`/"
    "`with`/`async def` block has its body indented at least 4 spaces "
    "deeper than the header. Preserve every non-whitespace character "
    "byte-exact. Parser error: {error}\n\n"
    "Return ONLY the fixed code with NO fences, NO commentary, NO "
    "preamble.\n\n"
    "```python\n{body}\n```"
)


@infra.langfuse.prompts.with_langfuse_override("dd.synth.render.normalize_python_retry")
def build_normalize_python_retry(
    *,
    body: str,
    error: str,
) -> str:
    return _NORMALIZE_PROMPT_PYTHON_RETRY.format(
        body = body,
        error = error,
    )
