"""LLM reference context generator — llms.txt (Jinja2)."""

from __future__ import annotations

import jinja2

from ._io import group_by_package, normalize_type

GENERATOR_NAME    = "uips-fixtures catalog"
GENERATOR_VERSION = "v0.1"

# ── Template ──────────────────────────────────────────────────────────────────

_MD_TEMPLATE = """\
# {{ pkg_id }} {{ src.get("version", "") }}

> generator: {{ generator_name }} {{ generator_version }}
> generated: {{ generated_at }}
> set: {{ set_id }}
> activities: {{ activities | length }}
{% if src.get("authors") %}> authors: {{ src["authors"] }}
{% endif %}
**ID format**: `{fullName}@{packageId}/{packageVersion}`
**Argument directions**: In (input), Out (output), InOut (bidirectional)

---
{% for act in activities %}
{% set name = act.get("displayName") or act["fullName"].split(".")[-1] %}
{% set members = act.get("members", []) %}
{% set args = members | selectattr("memberKind", "equalto", "argument") | list %}
{% set props = members | rejectattr("memberKind", "equalto", "argument") | list %}
### {{ name }}

**ID**: `{{ act["id"] }}`
**Type**: `{{ act["fullName"] }}`
{% if act.get("category") -%}
**Category**: {{ act["category"] }}
{% endif -%}
{% if act.get("description") -%}
**Description**: {{ act["description"] }}
{% endif %}
{% if args -%}
**Arguments**:
{% for m in args -%}
{% set d = m.get("argumentDirection") or "?" -%}
{% set req = " [required]" if m.get("isRequiredArgument") else "" -%}
{% set desc_part = " \u2014 " + m["description"] if m.get("description") else "" -%}
  - `{{ m["name"] }}` ({{ d }}{{ req }}): `{{ m["dataType"] | normalize_type }}`{{ desc_part }}
{% endfor %}
{% endif -%}
{% if props -%}
**Other members**:
{% for m in props -%}
{% set desc_part = " \u2014 " + m["description"] if m.get("description") else "" -%}
  - `{{ m["name"] }}` [{{ m["memberKind"] }}]: `{{ m["dataType"] | normalize_type }}`{{ desc_part }}
{% endfor %}
{% endif -%}
{% if not members -%}
_No members extracted._
{% endif %}
{% endfor %}
---
"""

# ── Jinja2 env ────────────────────────────────────────────────────────────────

_env = jinja2.Environment(
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_env.filters["normalize_type"] = normalize_type
_tmpl = _env.from_string(_MD_TEMPLATE)

# ── Public API ────────────────────────────────────────────────────────────────

def build_refctx(set_id: str, pkg_id: str, src: dict, activities: list[dict], generated_at: str) -> str:
    return _tmpl.render(
        set_id=set_id,
        pkg_id=pkg_id,
        src=src,
        activities=activities,
        generated_at=generated_at,
        generator_name=GENERATOR_NAME,
        generator_version=GENERATOR_VERSION,
    )
