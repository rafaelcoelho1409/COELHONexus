"""render — chapter template and pure markdown rendering helpers."""
from __future__ import annotations
from . import domain, patterns

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


def render_chapter_md(chapter_title: str, sections_ctx: list[dict]) -> str:
    """Render chapter Markdown deterministically from section contexts."""
    toc = domain.build_toc(sections_ctx) if len(sections_ctx) >= 2 else []
    template = JINJA_ENV.from_string(CHAPTER_MD_TEMPLATE)
    markdown = template.render(
        chapter_title = chapter_title,
        sections = sections_ctx,
        toc = toc,
    )
    markdown = patterns.EXCESSIVE_NEWLINES_RE.sub("\n\n\n", markdown)
    return domain.strip_outer_wrapper_fences(markdown).rstrip() + "\n"
