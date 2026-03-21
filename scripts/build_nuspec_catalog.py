"""
build_nuspec_catalog.py — Parse all cached .nuspec files in data/nuspec/ and
produce data/sources/nuget/nuspec_catalog.json (canonical Shape A).

Does ONE thing: parse cached artifacts → catalog.
Fetching is handled by collect_nuspec.py.

Usage (from repo root):
    uv run scripts/build_nuspec_catalog.py
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).resolve().parent.parent
NUSPEC_DIR  = REPO_ROOT / "data" / "nuspec"
CATALOG_OUT = REPO_ROOT / "data" / "sources" / "nuget" / "nuspec_catalog.json"

# All known nuspec namespace versions
NUSPEC_NAMESPACES = [
    "http://schemas.microsoft.com/packaging/2010/07/nuspec.xsd",
    "http://schemas.microsoft.com/packaging/2011/08/nuspec.xsd",
    "http://schemas.microsoft.com/packaging/2012/06/nuspec.xsd",
    "http://schemas.microsoft.com/packaging/2013/01/nuspec.xsd",
    "http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd",
    "",  # no namespace fallback
]

# ── Target framework normalisation ────────────────────────────────────────────

def normalize_target_framework(tf: str) -> str:
    """
    Normalize a NuGet targetFramework string to a short canonical name.

    Examples:
      .NETFramework4.6.1      -> net461
      net6.0-windows7.0       -> net6.0-windows
      net5.0-windows7.0       -> net5.0-windows
      net6.0                  -> net6.0
    """
    if not tf:
        return ""
    m = re.match(r"\.NETFramework(\d+)\.(\d+)(?:\.(\d+))?", tf)
    if m:
        major, minor, patch = m.group(1), m.group(2), m.group(3) or ""
        return f"net{major}{minor}{patch}"
    # Strip OS version suffix: net6.0-windows7.0 -> net6.0-windows
    tf = re.sub(r"(-windows)\d+\.\d+", r"\1", tf)
    return tf

# ── Studio version extraction ─────────────────────────────────────────────────

def parse_studio_version(description: str) -> str:
    """
    Extract minimum Studio version from nuspec description free text.
    Returns the version string (e.g. 'v2020.4', '20.10') or '' if not found.

    Handles:
      'requires Studio vX.Y or above'
      'requires Studio vX.Y.Z / vX.Y or above'  -> takes second (higher)
      'requires Studio X.Y or higher'
    """
    # Dual version: 'vX.Y.Z / vX.Y' — take the second (higher)
    m = re.search(
        r"requires Studio\s+v[\d.]+\s*/\s*(v[\d.]+)\s+or\s+(?:above|higher)",
        description,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"requires Studio\s+(v?[\d.]+)\s+or\s+(?:above|higher)",
        description,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return ""

# ── nuspec XML parsing ────────────────────────────────────────────────────────

def _find(parent: ET.Element, tag: str, ns_prefix: str) -> ET.Element | None:
    el = parent.find(f"{ns_prefix}{tag}")
    return el if el is not None else parent.find(tag)


def _text(parent: ET.Element, tag: str, ns_prefix: str) -> str:
    el = _find(parent, tag, ns_prefix)
    return (el.text or "").strip() if el is not None else ""


def _parse_deps(deps_el: ET.Element, ns_prefix: str) -> list[dict]:
    groups = []
    for group in (deps_el.findall(f"{ns_prefix}group") or deps_el.findall("group")):
        deps_in = group.findall(f"{ns_prefix}dependency") or group.findall("dependency")
        tf = normalize_target_framework(group.get("targetFramework", ""))
        groups.append({
            "targetFramework": tf,
            "dependencies": [
                {"id": d.get("id", ""), "version": d.get("version", "")}
                for d in deps_in
            ],
        })
    # Flat (non-grouped) dependencies
    flat = deps_el.findall(f"{ns_prefix}dependency") or deps_el.findall("dependency")
    if flat:
        groups.append({
            "targetFramework": "",
            "dependencies": [
                {"id": d.get("id", ""), "version": d.get("version", "")}
                for d in flat
            ],
        })
    return groups


def parse_nuspec(path: Path) -> dict | None:
    try:
        xml_text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    for ns in NUSPEC_NAMESPACES:
        prefix = f"{{{ns}}}" if ns else ""
        try:
            root = ET.fromstring(xml_text)
            meta = _find(root, "metadata", prefix)
            if meta is None:
                continue

            description = _text(meta, "description", prefix)

            deps_el = _find(meta, "dependencies", prefix)
            dep_groups = _parse_deps(deps_el, prefix) if deps_el is not None else []

            return {
                "id":                    _text(meta, "id", prefix),
                "version":               _text(meta, "version", prefix),
                "authors":               _text(meta, "authors", prefix),
                "description":           description,
                "tags":                  _text(meta, "tags", prefix),
                "projectUrl":            _text(meta, "projectUrl", prefix),
                "license":               _extract_license(meta, prefix),
                "minClientVersion":      root.get("minClientVersion", ""),
                "studioVersionMin":      parse_studio_version(description),
                "releaseNotes":          _text(meta, "releaseNotes", prefix)[:500],
                "dependencyGroups":      dep_groups,
            }
        except ET.ParseError:
            continue

    return None


def _extract_license(meta: ET.Element, prefix: str) -> str:
    lic = _find(meta, "license", prefix)
    if lic is not None:
        return (lic.text or "").strip() or lic.get("type", "")
    return _text(meta, "licenseUrl", prefix)

# ── Catalog build ─────────────────────────────────────────────────────────────

def build_catalog() -> None:
    now = datetime.now(timezone.utc)
    print(f"build_nuspec_catalog.py\nScanning {NUSPEC_DIR} …\n")

    items: list[dict] = []
    pkg_ids: set[str] = set()

    for nuspec_path in sorted(NUSPEC_DIR.rglob("*.nuspec")):
        record = parse_nuspec(nuspec_path)
        if not record:
            print(f"  [skip] {nuspec_path.relative_to(REPO_ROOT)}")
            continue

        # Merge manifest data
        manifest_path = nuspec_path.with_suffix(".manifest.json")
        if manifest_path.exists():
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)

            # Collect TFMs from lib + ref, normalise
            tfms: set[str] = set()
            for section in ("lib", "ref"):
                for tfm in manifest.get(section, {}):
                    n = normalize_target_framework(tfm)
                    if n:
                        tfms.add(n)

            record["targetFrameworks"] = sorted(tfms)
            record["assemblies"] = {
                "lib": manifest.get("lib", {}),
                "ref": manifest.get("ref", {}),
            }
            record["hasAnalyzers"]    = bool(manifest.get("analyzers"))
            record["hasBuildProps"]   = nuspec_path.with_suffix(".props").exists()
            record["hasBuildTargets"] = nuspec_path.with_suffix(".targets").exists()
        else:
            record["targetFrameworks"] = []
            record["assemblies"]       = {"lib": {}, "ref": {}}
            record["hasAnalyzers"]     = False
            record["hasBuildProps"]    = False
            record["hasBuildTargets"]  = False

        if record.get("id"):
            pkg_ids.add(record["id"])
        items.append(record)

    catalog = {
        "schema": {"version": "1.0", "domain": "nuget", "type": "nuspec-catalog"},
        "meta": {
            "collectedAtUtc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source":         "data/nuspec/",
            "description":    "Parsed nuspec descriptors and manifests for all cached packages",
        },
        "stats": {
            "nuspecCount":  len(items),
            "packageCount": len(pkg_ids),
        },
        "items": items,
    }

    CATALOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CATALOG_OUT, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
        f.write("\n")

    print(f"Written: {CATALOG_OUT.relative_to(REPO_ROOT)}"
          f" ({len(items)} entries, {len(pkg_ids)} packages)")


if __name__ == "__main__":
    build_catalog()
