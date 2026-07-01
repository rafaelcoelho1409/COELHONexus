"""render — Jinja2 environment + inline chapter template."""
from __future__ import annotations

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


