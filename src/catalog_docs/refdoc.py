"""HTML developer reference generator (Jinja2)."""

from __future__ import annotations

import jinja2
import markupsafe

from ._io import group_by_package, normalize_type

GENERATOR_NAME    = "uips-fixtures catalog"
GENERATOR_VERSION = "v0.1"

# ── CSS (Solarized Light) ──────────────────────────────────────────────────────

_CSS = """\
    :root {
      --base3:  #fdf6e3; --base2: #eee8d5; --base1: #93a1a1;
      --base00: #657b83; --base01: #586e75;
      --orange: #cb4b16; --blue: #268bd2; --cyan: #2aa198;
      --green:  #859900;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: var(--base3); color: var(--base00);
      line-height: 1.55; padding: 0 0 4rem;
    }
    .topbar {
      background: var(--orange); color: #fff;
      padding: 0 2rem; height: 50px;
      display: flex; align-items: center; gap: 1rem;
      font-weight: 700; font-size: 1rem;
    }
    .topbar-sub { font-weight: 400; font-size: 0.82rem; opacity: 0.82; }
    .content { max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem 0; }
    .gen-meta {
      font-size: 0.78rem; color: var(--base1);
      border-bottom: 1px solid var(--base2);
      padding-bottom: 0.75rem; margin-bottom: 2rem;
    }
    .pkg-section { margin-bottom: 3rem; }
    .pkg-heading {
      font-size: 1.05rem; font-weight: 700;
      color: var(--base01); border-bottom: 2px solid var(--orange);
      padding-bottom: 0.3rem; margin-bottom: 1.25rem;
    }
    .pkg-version { font-weight: 400; font-size: 0.82rem; color: var(--base1); }
    .activity {
      background: #fff; border: 1px solid var(--base2);
      border-radius: 4px; padding: 1rem 1.25rem; margin-bottom: 1rem;
    }
    .activity-name { font-size: 1rem; font-weight: 700; color: var(--base01); margin-bottom: 0.15rem; }
    .activity-full { font-family: ui-monospace, monospace; font-size: 0.78rem; color: var(--base1); margin-bottom: 0.5rem; }
    .activity-desc { font-size: 0.875rem; margin-bottom: 0.75rem; }
    .badge {
      display: inline-block; font-size: 0.72rem; padding: 0.1em 0.5em;
      border-radius: 3px; margin-right: 0.35rem; margin-bottom: 0.5rem;
      background: var(--base2); color: var(--base01);
    }
    .badge-cat { background: #d4edda; color: #155724; }
    .badge-src { background: #cce5ff; color: #004085; }
    .members-heading {
      font-size: 0.72rem; text-transform: uppercase;
      letter-spacing: 0.07em; color: var(--base1);
      margin: 0.75rem 0 0.35rem;
    }
    table { border-collapse: collapse; width: 100%; font-size: 0.82rem; }
    th, td { text-align: left; padding: 0.3rem 0.6rem; border-bottom: 1px solid var(--base2); }
    th { background: var(--base2); color: var(--base01); font-size: 0.75rem; font-weight: 600; }
    tr:last-child td { border-bottom: none; }
    .dir-in    { color: var(--green);  font-weight: 600; }
    .dir-out   { color: var(--blue);   font-weight: 600; }
    .dir-inout { color: var(--cyan);   font-weight: 600; }
    .req       { color: var(--orange); font-weight: 700; }
    code { background: var(--base2); padding: 0.1em 0.35em; border-radius: 3px;
           font-size: 0.85em; font-family: ui-monospace, monospace; }
    .no-members { font-size: 0.8rem; color: var(--base1); font-style: italic; }"""

# ── Template ──────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{{ pkg_id }} {{ src.get("version", "") }} \u2014 Activity Reference</title>
  <!-- {{ generator_name }} {{ generator_version }} | generated {{ generated_at }} -->
  <style>
{{ css }}
  </style>
</head>
<body>
  <header class="topbar">
    <span>uips-fixtures</span>
    <span class="topbar-sub">{{ pkg_id }} {{ src.get("version", "") }} &mdash; {{ set_id }}</span>
  </header>
  <div class="content">
    <p class="gen-meta">
      {{ activities | length }} activities &middot;
      {{ generator_name }} {{ generator_version }} &middot; generated {{ generated_at }}
    </p>
    {% for act in activities %}
    {% set name = act.get("displayName") or act["fullName"].split(".")[-1] %}
    {% set args = act.get("members", []) | selectattr("memberKind", "equalto", "argument") | list %}
    {% set props = act.get("members", []) | rejectattr("memberKind", "equalto", "argument") | list %}
    <div class="activity" id="{{ act['id'] }}">
      <div class="activity-name">{{ name }}</div>
      <div class="activity-full">{{ act["fullName"] }}</div>
      <span class="badge badge-src">{{ pkg_id }} {{ src.get("version", "") }}</span>
      {% if act.get("category") %}<span class="badge badge-cat">{{ act["category"] }}</span>{% endif %}
      {% if act.get("description") %}<p class="activity-desc">{{ act["description"] }}</p>{% endif %}
      {% if args %}
      <p class="members-heading">Arguments</p>
      <table>
        <tr><th>Name</th><th>Dir</th><th>Type</th><th>Req</th><th>Description</th></tr>
        {% for m in args %}
        <tr>
          <td>{{ m["displayName"] }}</td>
          <td>{{ m.get("argumentDirection") | dir_span }}</td>
          <td><code>{{ m["dataType"] | normalize_type }}</code></td>
          <td>{% if m.get("isRequiredArgument") %}<span class="req">&#10003;</span>{% endif %}</td>
          <td>{{ m.get("description") or "" }}</td>
        </tr>
        {% endfor %}
      </table>
      {% endif %}
      {% if props %}
      <p class="members-heading">Properties / child slots</p>
      <table>
        <tr><th>Name</th><th>Kind</th><th>Type</th><th>Description</th></tr>
        {% for m in props %}
        <tr>
          <td>{{ m["displayName"] }}</td>
          <td>{{ m["memberKind"] }}</td>
          <td><code>{{ m["dataType"] | normalize_type }}</code></td>
          <td>{{ m.get("description") or "" }}</td>
        </tr>
        {% endfor %}
      </table>
      {% endif %}
      {% if not act.get("members") %}
      <p class="no-members">No members extracted.</p>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</body>
</html>
"""

# ── Jinja2 env ────────────────────────────────────────────────────────────────

def _dir_span(d: str | None) -> markupsafe.Markup:
    mapping = {
        "In":    '<span class="dir-in">In</span>',
        "Out":   '<span class="dir-out">Out</span>',
        "InOut": '<span class="dir-inout">InOut</span>',
    }
    return markupsafe.Markup(mapping.get(d or "", "&mdash;"))


_env = jinja2.Environment(
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["normalize_type"] = normalize_type
_env.filters["dir_span"] = _dir_span
_tmpl = _env.from_string(_HTML_TEMPLATE)

# ── Public API ────────────────────────────────────────────────────────────────

def iter_packages(catalogs: list[dict]) -> list[tuple[str, dict, list[dict]]]:
    """Return [(pkg_id, src, activities), ...] in source order.

    Accepts the list of per-package catalog dicts returned by load_catalog().
    Each entry has a top-level 'source' dict and an 'activities' list.
    """
    return [
        (entry["source"]["id"], entry["source"], entry["activities"])
        for entry in catalogs
    ]


def build_html(set_id: str, pkg_id: str, src: dict, activities: list[dict], generated_at: str) -> str:
    return _tmpl.render(
        set_id=set_id,
        pkg_id=pkg_id,
        src=src,
        activities=activities,
        generated_at=generated_at,
        generator_name=GENERATOR_NAME,
        generator_version=GENERATOR_VERSION,
        css=markupsafe.Markup(_CSS),
    )
