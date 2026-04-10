# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2", "packaging"]
# ///
"""
build_refdoc.py — Generate HTML reference and llms.txt index from activity-catalog dist.

Reads data/dist/llms/{pkg-id}/{version}.json (latest stable per package) and writes:

  data/dist/refdoc/{pkg-id}/index.html   — per-package activity reference
  data/dist/refdoc/index.html            — all-packages listing
  data/dist/index.html                   — root landing page
  data/dist/llms/llms.txt                — root llms.txt: all packages + versions

Usage:
    uv run scripts/build_refdoc.py
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

import jinja2
import markupsafe
from packaging.version import Version

REPO_ROOT    = Path(__file__).resolve().parent.parent
LLMS_DIR     = REPO_ROOT / "data" / "dist" / "llms"
REFDOC_DIR   = REPO_ROOT / "data" / "dist" / "refdoc"
DIST_DIR     = REPO_ROOT / "data" / "dist"

GENERATOR_NAME    = "uips-fixtures catalog"
GENERATOR_VERSION = "v0.3"

# ── Version resolution ────────────────────────────────────────────────────────

def _valid_version(v: str) -> bool:
    try:
        Version(v)
        return True
    except Exception:
        return False


def _latest_stable(versions: list[str]) -> str | None:
    stable = []
    for v in versions:
        if re.search(r"[a-zA-Z]", v):
            continue
        try:
            stable.append((Version(v), v))
        except Exception:
            pass
    return max(stable)[1] if stable else None


def discover_packages() -> list[tuple[str, str, list[str]]]:
    """Return [(pkg_id, latest_version, all_versions), ...] sorted by pkg_id."""
    result = []
    if not LLMS_DIR.exists():
        return result
    for pkg_dir in sorted(LLMS_DIR.iterdir()):
        if not pkg_dir.is_dir():
            continue
        versions = [f.stem for f in pkg_dir.glob("*.json")]
        if not versions:
            continue
        latest = _latest_stable(versions)
        if latest is None:
            continue
        all_sorted = sorted(
            versions,
            key=lambda v: (1, Version(v)) if (not re.search(r"[a-zA-Z]", v) and _valid_version(v)) else (0, v),
            reverse=True,
        )
        result.append((pkg_dir.name, latest, all_sorted))
    return result


def load_catalog(pkg_id: str, version: str) -> dict:
    path = LLMS_DIR / pkg_id / f"{version}.json"
    return json.loads(path.read_text(encoding="utf-8"))

# ── Type helper ───────────────────────────────────────────────────────────────

def normalize_type(dt: str | None) -> str:
    if not dt:
        return ""
    for prefix in (
        "System.Activities.",
        "System.Collections.Generic.",
        "System.Collections.ObjectModel.",
        "System.",
    ):
        dt = dt.replace(prefix, "")
    return dt

# ── CSS ───────────────────────────────────────────────────────────────────────

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
    .topbar a { color: #fff; text-decoration: none; }
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
    .no-members { font-size: 0.8rem; color: var(--base1); font-style: italic; }
    .pkg-list { list-style: none; columns: 2; }
    .pkg-list li { padding: 0.2rem 0; }
    .pkg-list a { color: var(--blue); text-decoration: none; }
    .pkg-list a:hover { text-decoration: underline; }"""

# ── Per-package HTML template ─────────────────────────────────────────────────

_PKG_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{{ pkg_id }} {{ version }} — Activity Reference</title>
  <style>
{{ css }}
  </style>
</head>
<body>
  <header class="topbar">
    <a href="../../index.html">uips-fixtures</a>
    <span class="topbar-sub">{{ pkg_id }} {{ version }}</span>
  </header>
  <div class="content">
    <p class="gen-meta">
      {{ activities | length }} activities &middot;
      {{ generator_name }} {{ generator_version }} &middot; generated {{ generated_at }}
    </p>
    {% for act in activities %}
    {% set name = act.get("displayName") or act["fullName"].split(".")[-1] %}
    {% set s = act.get("members", []) | split_members %}
    <div class="activity" id="{{ act['fullName'] | replace('.', '-') }}">
      <div class="activity-name">{{ name }}</div>
      <div class="activity-full">{{ act["fullName"] }}</div>
      {% if act.get("category") %}<span class="badge badge-cat">{{ act["category"] }}</span>{% endif %}
      {% if act.get("description") %}<p class="activity-desc">{{ act["description"] }}</p>{% endif %}
      {% for cat, group in s["arg_categories"].items() %}
      <p class="members-heading">Arguments{% if cat %} — {{ cat }}{% endif %}</p>
      <table>
        <tr><th>Name</th><th>Dir</th><th>Type</th><th>Req</th><th>Default</th><th>Description</th></tr>
        {% for m in group %}
        <tr>
          <td>{{ m.get("displayName") or m["name"] }}</td>
          <td>{{ m.get("argumentDirection") | dir_span }}</td>
          <td><code>{{ m["dataType"] | normalize_type }}</code></td>
          <td>{% if m.get("isRequiredArgument") %}<span class="req">&#10003;</span>{% endif %}</td>
          <td>{% if m.get("defaultValue") %}<code>{{ m["defaultValue"] }}</code>{% endif %}</td>
          <td>{{ m.get("description") or "" }}</td>
        </tr>
        {% endfor %}
      </table>
      {% endfor %}
      {% if s["props"] %}
      <p class="members-heading">Properties</p>
      <table>
        <tr><th>Name</th><th>Kind</th><th>Type</th><th>Description</th></tr>
        {% for m in s["props"] %}
        <tr>
          <td>{{ m.get("displayName") or m["name"] }}</td>
          <td>{{ m["memberKind"] }}</td>
          <td><code>{{ m["dataType"] | normalize_type }}</code></td>
          <td>{{ m.get("description") or "" }}</td>
        </tr>
        {% endfor %}
      </table>
      {% endif %}
      {% if s["special"] %}
      <p class="members-heading">Child Activities / Variable Scope</p>
      <table>
        <tr><th>Name</th><th>Kind</th><th>Type</th><th>Description</th></tr>
        {% for m in s["special"] %}
        <tr>
          <td>{{ m.get("displayName") or m["name"] }}</td>
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
        "in":     '<span class="dir-in">In</span>',
        "out":    '<span class="dir-out">Out</span>',
        "in-out": '<span class="dir-inout">InOut</span>',
    }
    return markupsafe.Markup(mapping.get(d or "", "&mdash;"))


def _split_members(members: list[dict]) -> dict:
    args    = [m for m in members if m.get("memberKind") == "argument"]
    props   = [m for m in members if m.get("memberKind") not in ("argument", "variable-scope", "child")]
    special = [m for m in members if m.get("memberKind") in ("variable-scope", "child")]
    cats: dict[str, list[dict]] = {}
    for m in args:
        cats.setdefault(m.get("category") or "", []).append(m)
    return {"arg_categories": cats, "props": props, "special": special}


_env = jinja2.Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
_env.filters["normalize_type"] = normalize_type
_env.filters["dir_span"]       = _dir_span
_env.filters["split_members"]  = _split_members
_pkg_tmpl = _env.from_string(_PKG_TEMPLATE)

# ── HTML writers ──────────────────────────────────────────────────────────────

def write_pkg_html(pkg_id: str, version: str, catalog: dict, generated_at: str) -> None:
    activities = catalog.get("activities", [])
    html = _pkg_tmpl.render(
        pkg_id=pkg_id,
        version=version,
        activities=activities,
        generated_at=generated_at,
        generator_name=GENERATOR_NAME,
        generator_version=GENERATOR_VERSION,
        css=markupsafe.Markup(_CSS),
    )
    out = REFDOC_DIR / pkg_id / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"  refdoc/{pkg_id}/index.html: {len(activities)} activities")


def write_refdoc_index(packages: list[tuple[str, str, list[str]]], generated_at: str) -> None:
    rows = "".join(
        f'<li><a href="{pkg_id}/index.html">{pkg_id}</a> '
        f'<span class="topbar-sub">{version}</span></li>\n'
        for pkg_id, version, _ in packages
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Activity Reference — uips-fixtures</title>
  <style>
{_CSS}
  </style>
</head>
<body>
  <header class="topbar">
    <a href="../index.html">uips-fixtures</a>
    <span class="topbar-sub">Activity Reference</span>
  </header>
  <div class="content">
    <p class="gen-meta">
      {len(packages)} packages &middot;
      {GENERATOR_NAME} {GENERATOR_VERSION} &middot; generated {generated_at}
    </p>
    <ul class="pkg-list">
{rows}    </ul>
  </div>
</body>
</html>
"""
    out = REFDOC_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"  refdoc/index.html: {len(packages)} packages")


def write_root_index(packages: list[tuple[str, str, list[str]]], generated_at: str) -> None:
    rows = "".join(
        f'<li><a href="refdoc/{pkg_id}/index.html">{pkg_id}</a> '
        f'<span class="topbar-sub">{version}</span></li>\n'
        for pkg_id, version, _ in packages
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Activity Catalog — uips-fixtures</title>
  <style>
{_CSS}
  </style>
</head>
<body>
  <header class="topbar">
    <span>uips-fixtures</span>
    <span class="topbar-sub">Activity Catalog</span>
  </header>
  <div class="content">
    <p class="gen-meta">
      {len(packages)} packages &middot;
      {GENERATOR_NAME} {GENERATOR_VERSION} &middot; generated {generated_at}
    </p>
    <ul class="pkg-list">
{rows}    </ul>
  </div>
</body>
</html>
"""
    out = DIST_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"  index.html: {len(packages)} packages")


def write_root_llms_txt(packages: list[tuple[str, str, list[str]]], generated_at: str) -> None:
    """Root llms.txt: all packages with their available versions."""
    lines = [
        "# uips-fixtures activity catalog",
        "",
        "> per-package catalogs for UiPath activity XAML generation",
        f"> generated: {generated_at}",
        "",
        "## Packages",
        "",
    ]
    for pkg_id, latest, all_versions in packages:
        lines.append(f"- [{pkg_id}]({pkg_id}/llms.txt) — latest: {latest}")
    lines.append("")
    out = LLMS_DIR / "llms.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  llms/llms.txt: {len(packages)} packages")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    packages = discover_packages()
    if not packages:
        print("[error] No packages found in data/dist/llms/. Run build_dist.py first.", file=sys.stderr)
        sys.exit(1)

    generated_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for pkg_id, latest, all_versions in packages:
        catalog = load_catalog(pkg_id, latest)
        write_pkg_html(pkg_id, latest, catalog, generated_at)

    write_refdoc_index(packages, generated_at)
    write_root_index(packages, generated_at)
    write_root_llms_txt(packages, generated_at)

    print(f"\nDone: {len(packages)} packages")


if __name__ == "__main__":
    main()
