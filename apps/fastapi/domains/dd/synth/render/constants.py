"""render — constants, compiled regexes, Jinja2 environment, templates."""
from __future__ import annotations

import re

from jinja2 import Environment, StrictUndefined


# =============================================================================
# Versioning
# =============================================================================
RENDER_SCHEMA_VERSION = "2.0-cookbook"
# v3 (2026-05-29): added the write-path dedupe_and_align_sections pass
# (cross-section code recycling + misrouted-block omission). Bumped so the
# render cache invalidates and chapters re-render through the new pass.
RENDER_TEMPLATE_VERSION = "v3-dedup-align-2026-05-29"

# Same algorithm as `synth/vault.py:_hash_block` — 16-hex SHA-256
# prefix. MUST match or the audit will false-fail. If vault.py ever
# changes the prefix length, bump _VAULT_HASH_LEN here too.
_VAULT_HASH_LEN = 16
_HASH_ALGO = "sha256"

# Sentinel pattern from `synth/vault.py:_make_sentinel`. Used to scan
# the rendered output for ANY unresolved sentinels (would indicate a
# materialization bug). Lang attribute is optional (vault.py emits it
# only when lang is non-empty).
_SENTINEL_RE = re.compile(
    r'<code-ref hash="([0-9a-f]{16})"(?:\s+lang="[^"]*")?\s*/>'
)


# =============================================================================
# Jinja2 templates (inline)
# =============================================================================
# Render env — autoescape OFF (we produce markdown, not HTML). Strict
# undefined so an unset template var crashes loudly instead of producing
# silent `None` literals in the output.
_JINJA_ENV = Environment(
    autoescape=False,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    lstrip_blocks=True,
    trim_blocks=True,
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
