"""render — constants, compiled regexes, Jinja2 environment, templates."""
from __future__ import annotations

import re

from jinja2 import Environment, StrictUndefined


# =============================================================================
# Versioning
# =============================================================================
RENDER_SCHEMA_VERSION = "1.0"
RENDER_TEMPLATE_VERSION = "v1-2026-05-19"

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

{% for section in sections %}
## {{ section.heading }}

{% for paragraph in section.paragraphs %}
{{ paragraph }}

{% endfor -%}
{% if section.materialized_code_blocks -%}
{% for code_block in section.materialized_code_blocks %}
{{ code_block }}

{% endfor -%}
{% endif -%}
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
