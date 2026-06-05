"""render — Jinja2 environment + inline chapter/challenges templates."""
from __future__ import annotations

from jinja2 import Environment, StrictUndefined


# Render env — autoescape OFF (we produce markdown, not HTML). Strict
# undefined so an unset template var crashes loudly instead of producing
# silent `None` literals in the output.
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


CHALLENGES_MD_TEMPLATE = """\
# Active Recall Questions — {{ chapter_title }}

{% for q in challenges %}
{{ loop.index }}. {{ q }}
{% endfor %}
"""
