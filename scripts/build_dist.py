# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "packaging"]
# ///
"""
build_dist.py — Transform PackageFurnace enriched-catalog into activity-catalog v0.3 JSON.

Reads config/curated.yaml, resolves package versions, reads enriched-catalog files from
data/sources/packagefurnace/pkg/{id}/{version}.json, maps activities/members/enums/
namespaceMappings, and writes:

  data/dist/packages/{pkg-id}/{version}.json   — per-package/version catalog
  data/dist/packages/{pkg-id}/llms.txt         — version index (one per package)

Output is set-independent: the same (pkg-id, version) is built once regardless of
how many curated sets reference it.

Usage:
    uv run scripts/build_dist.py                    # all packages across all sets
    uv run scripts/build_dist.py --set watchful-anvil
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import yaml
from packaging.version import Version

# ── Paths ─────────────────────────────────────────────────────────────────────

import os

REPO_ROOT      = Path(__file__).resolve().parent.parent
SHAPE_B_DIR    = REPO_ROOT / "data" / "sources" / "packagefurnace" / "pkg"
FILESYSTEM_DIR = REPO_ROOT / "data" / "sources" / "filesystem" / "pkg"
PKG_DATA_DIR   = REPO_ROOT / "data" / "pkg"
OUT_LLMS_DIR         = REPO_ROOT / "data" / "dist" / "llms"
CONFIG_PATH          = REPO_ROOT / "config" / "curated.yaml"
VISIBILITY_RULES_PATH = REPO_ROOT / "config" / "visibility_rules.yaml"

# Optional pf-cache root — when set, enriched-catalog.json is preferred over plain engine-result.
# Set via PF_CACHE env var (same as used by seed scripts).
_PF_CACHE = Path(os.environ["PF_CACHE"]) if "PF_CACHE" in os.environ else None


def _resolve_source_path(pkg_id: str, version: str) -> Path:
    """Return the best available source JSON for (pkg_id, version).

    Preference order:
    1. pf-cache enriched-catalog.json  (when PF_CACHE is set and file exists)
    2. data/sources/packagefurnace/    (plain engine-result, always present after seeding)
    """
    if _PF_CACHE is not None:
        enriched = _PF_CACHE / pkg_id.lower() / version / "enriched-catalog.json"
        if enriched.exists():
            return enriched
    return SHAPE_B_DIR / pkg_id / f"{version}.json"

# ── Config ────────────────────────────────────────────────────────────────────

config    = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
tfm_allow = set(config["defaults"]["targetFrameworks"]["include"])

_vis_rules = yaml.safe_load(VISIBILITY_RULES_PATH.read_text(encoding="utf-8"))
_legacy_suppress_prefixes: tuple[str, ...] = tuple(
    entry["namespace_prefix"]
    for entry in (_vis_rules.get("legacy_suppress") or [])
)

# ── Version resolution ────────────────────────────────────────────────────────

def resolve_versions(pkg_id: str, pkg_cfg: dict) -> list[str]:
    """Return list of versions to include for this package entry."""
    if "version" in pkg_cfg:
        return [pkg_cfg["version"]]
    if "versions" in pkg_cfg:
        return list(pkg_cfg["versions"])
    # latest-stable: highest non-prerelease version present in either source dir
    available: set[str] = set()
    for src_dir in (SHAPE_B_DIR, FILESYSTEM_DIR):
        pkg_dir = src_dir / pkg_id
        if pkg_dir.exists():
            available.update(p.stem for p in pkg_dir.glob("*.json"))
    stable = [v for v in available if not re.search(r"[a-zA-Z]", v)]
    if not stable:
        return []
    return [max(stable, key=Version)]

# ── TFM validation ─────────────────────────────────────────────────────────────

def _normalize_tfm(tfm: str) -> str:
    """net6.0-windows7.0 → net6.0-windows  (strip platform version suffix)."""
    if "-" not in tfm:
        return tfm
    base, platform = tfm.rsplit("-", 1)
    platform = re.sub(r"\d+(\.\d+)*$", "", platform)  # windows7.0 → windows
    return f"{base}-{platform}"


def check_tfm(pkg_id: str, version: str) -> None:
    """Warn if the package has lib TFMs but none match the allowlist. Never skips."""
    manifest_path = PKG_DATA_DIR / pkg_id / f"{version}.manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_tfms      = set(manifest.get("lib", {}).keys())
    normalised    = {_normalize_tfm(t) for t in raw_tfms}
    if raw_tfms and normalised.isdisjoint(tfm_allow):
        print(f"  [warn] {pkg_id} {version}: lib TFMs {raw_tfms} do not intersect "
              f"allowlist {tfm_allow}", file=sys.stderr)

# ── dataType normalisation ─────────────────────────────────────────────────────

_DVM_PREFIX = "System.Activities.DesignViewModels."

def normalize_datatype(dt: str) -> str:
    """
    Strip DesignViewModels wrappers so consumers see canonical WF4 types:
      DesignInArgument<T>    → InArgument<T>
      DesignOutArgument<T>   → OutArgument<T>
      DesignInOutArgument<T> → InOutArgument<T>
      DesignProperty<T>      → T   (plain property; wrapper carries no semantic)
    """
    for src, dst in (
        (_DVM_PREFIX + "DesignInArgument<",    "System.Activities.InArgument<"),
        (_DVM_PREFIX + "DesignOutArgument<",   "System.Activities.OutArgument<"),
        (_DVM_PREFIX + "DesignInOutArgument<", "System.Activities.InOutArgument<"),
    ):
        dt = dt.replace(src, dst)

    m = re.fullmatch(
        r"System\.Activities\.DesignViewModels\.DesignProperty<(.+)>", dt
    )
    if m:
        dt = m.group(1)

    return dt

# ── memberKind inference ───────────────────────────────────────────────────────

_CHILD_RE = re.compile(
    r"System\.Activities\."
    r"(Activity|ActivityAction|ActivityFunc|NativeActivity|AsyncCodeActivity|CodeActivity)"
    r"[\[<,]?"
)
_VAR_RE = re.compile(
    r"(System\.Collections\.ObjectModel\.Collection<System\.Activities\.Variable"
    r"|System\.Activities\.Variable[<,\[])"
)

def infer_member_kind(data_type: str | None, arg_dir: str | None) -> str:
    if arg_dir:
        return "argument"
    if data_type and _CHILD_RE.search(data_type):
        return "child"
    if data_type and _VAR_RE.search(data_type):
        return "variable-scope"
    return "property"

# ── Property → member mapping ──────────────────────────────────────────────────

_ARG_DIR_MAP = {0: "in", 1: "out", 2: "in-out"}


def _map_type_members(raw: list[dict]) -> list[dict]:
    """Apply map_property to nested typeMembers, filtering non-browsable entries."""
    result = []
    for p in raw:
        mapped = map_property(p)
        if mapped is not None:
            result.append(mapped)
    return result


def map_property(prop: dict) -> dict | None:
    """Return a member dict, or None if the property should be excluded.

    Field mapping from PackageFurnace engine-result schema v1:
      isBrowsable       (was: browsable)
      isRequired        (was: isRequiredArgument)
      members           (was: properties)
    """
    raw_dt  = prop.get("dataType")             # null when absent in source
    raw_dir = prop.get("argumentDirection")    # int (0/1/2) or str or None
    arg_dir = _ARG_DIR_MAP.get(raw_dir, raw_dir) if isinstance(raw_dir, int) else raw_dir
    dt      = normalize_datatype(raw_dt) if raw_dt else None
    kind    = infer_member_kind(dt, arg_dir)

    # Filter non-browsable members — but always keep child/variable-scope:
    # those are structural slots (activity body, variable declarations) that
    # must be serialized in XAML even when hidden from the Properties panel.
    if prop.get("isBrowsable") is False and kind not in ("child", "variable-scope"):
        return None

    return {
        "name":                  prop["name"],
        "displayName":           prop.get("displayName") or prop["name"],
        "dataType":              dt,
        "memberKind":            kind,
        "argumentDirection":     arg_dir if kind == "argument" else None,
        "isRequiredArgument":    bool(prop.get("isRequired")) if kind == "argument" else False,
        "description":           prop.get("description"),
        "category":              prop.get("category"),
        "defaultValue":          prop.get("defaultValue"),
        "typeConverter":         prop.get("typeConverter"),
        "isObsolete":            bool(prop.get("isObsolete", False)),
        "obsoleteMessage":       prop.get("obsoleteMessage"),
        "delegateArgumentName":  prop.get("delegateArgumentName"),
        "enumValues":            prop.get("enumValues") or [],
        "typeMembers":           _map_type_members(prop.get("typeMembers") or []),
    }

# ── Source and activity builders ───────────────────────────────────────────────

def build_source(shape_b: dict) -> dict:
    # engine-result: sourceId/sourceVersion live in requestedUnit
    ru  = shape_b.get("requestedUnit", shape_b)   # fallback to root for older format
    pkg = shape_b.get("package", {})
    return {
        "kind":         "nuget-package",
        "id":           ru["sourceId"],
        "version":      ru["sourceVersion"],
        "feedUrl":      shape_b.get("feedUrl") or None,
        "authors":      pkg.get("authors") or None,
        "projectUrl":   pkg.get("projectUrl") or None,
        "description":  pkg.get("description") or None,
        "license":      pkg.get("license") or None,
        "tags":         pkg.get("tags") or None,
        "packageTypes": pkg.get("packageTypes") or [],
    }

def build_activity(item: dict, source_obj: dict) -> dict:
    # engine-result v1: members (was: properties); only browsable/legacy activities are useful
    members = [
        m for p in item.get("members", [])
        if (m := map_property(p)) is not None
    ]
    return {
        "id":                    f"{item['fullName']}@{source_obj['id']}/{source_obj['version']}",
        "fullName":              item["fullName"],
        "displayName":           item.get("displayName"),
        "description":           item.get("description"),
        "category":              item.get("category"),
        "visibility":            item.get("visibility", "browsable"),
        "hasGenericParameters":  bool(item.get("hasGenericParameters", False)),
        "genericParameterNames": item.get("genericParameterNames") or [],
        "isObsolete":            bool(item.get("isObsolete", False)),
        "obsoleteMessage":       item.get("obsoleteMessage"),
        "xmlNamespace":          item.get("xmlNamespace"),
        "xmlPrefix":             item.get("xmlPrefix"),
        "members":               members,
    }


def build_enum(item: dict) -> dict:
    return {
        "fullName":       item["fullName"],
        "underlyingType": item["underlyingType"],
        "members": [
            {"name": m["name"], "value": m["value"]}
            for m in item.get("members", [])
        ],
    }


def build_xmlns_mapping(item: dict) -> dict:
    return {
        "xmlNamespace":  item["xmlNamespace"],
        "prefix":        item.get("prefix", ""),
        "clrNamespaces": item.get("clrNamespaces") or [],
        "sourceAssembly": item.get("sourceAssembly", ""),
    }

# ── Output helper ──────────────────────────────────────────────────────────────

def write_json(path: Path, obj: object) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")

# ── Visibility suppression ────────────────────────────────────────────────────

def _is_legacy_suppressed(item: dict) -> bool:
    """Return True if this activity should be suppressed per visibility_rules.yaml.

    Only applied to legacy-visibility activities — browsable activities from
    curated ActivitiesMetadata.json are never suppressed by namespace rules.
    """
    if item.get("visibility") != "legacy":
        return False
    full_name = item.get("fullName", "")
    return full_name.startswith(_legacy_suppress_prefixes)


# ── Package collectors ─────────────────────────────────────────────────────────

def _all_extracted_packages() -> list[dict]:
    """Return [{id, version}, ...] for every stable version present in SHAPE_B_DIR."""
    entries = []
    if not SHAPE_B_DIR.exists():
        return entries
    for pkg_dir in sorted(SHAPE_B_DIR.iterdir()):
        if not pkg_dir.is_dir():
            continue
        for json_file in sorted(pkg_dir.glob("*.json")):
            ver = json_file.stem
            if not re.search(r"[a-zA-Z]", ver):  # stable versions only
                entries.append({"id": pkg_dir.name, "version": ver})
    return entries


def _collect_pkg_versions(sets: list[dict]) -> dict[str, list[str]]:
    """Return {pkg_id: [version, ...]} deduplicated across all sets."""
    result: dict[str, list[str]] = {}
    for set_cfg in sets:
        pkg_list = _all_extracted_packages() if set_cfg.get("all_extracted") else set_cfg.get("packages", [])
        for pkg_cfg in pkg_list:
            pkg_id   = pkg_cfg["id"]
            versions = resolve_versions(pkg_id, pkg_cfg)
            for version in versions:
                if version not in result.get(pkg_id, []):
                    result.setdefault(pkg_id, []).append(version)
    return result


# ── llms.txt writer ────────────────────────────────────────────────────────────

def write_llms_txt(pkg_id: str, versions: list[str]) -> None:
    """Write data/dist/packages/{pkg_id}/llms.txt — a token-efficient version index."""
    def _version_key(v: str):
        try:
            return (1, Version(v))
        except Exception:
            return (0, v)

    sorted_versions = sorted(versions, key=_version_key, reverse=True)
    lines = [
        f"# {pkg_id}",
        "",
        "> activity catalog — fetch a version JSON to get all data needed for XAML generation",
        "",
        "## Versions",
        "",
    ]
    for v in sorted_versions:
        lines.append(f"- [{v}]({v}.json)")
    lines.append("")
    out_path = OUT_LLMS_DIR / pkg_id / "llms.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  {pkg_id}: llms.txt ({len(sorted_versions)} versions) -> {out_path}")


# ── Package builder ────────────────────────────────────────────────────────────

def build_package(pkg_id: str, version: str) -> dict | None:
    """Build and write one per-package/version catalog. Returns catalog dict or None on error."""
    shape_b_path = _resolve_source_path(pkg_id, version)
    if not shape_b_path.exists():
        print(f"  [error] missing source: {shape_b_path}", file=sys.stderr)
        return None

    check_tfm(pkg_id, version)

    generated_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    shape_b      = json.loads(shape_b_path.read_text(encoding="utf-8"))
    source_obj   = build_source(shape_b)

    _seen_act: dict[str, dict] = {}
    for item in shape_b.get("activities", []):
        if item.get("visibility") == "hidden":
            continue
        if _is_legacy_suppressed(item):
            continue
        act = build_activity(item, source_obj)
        if act is not None and act["fullName"] not in _seen_act:
            _seen_act[act["fullName"]] = act
    activities = list(_seen_act.values())

    seen_enums = {}
    for item in shape_b.get("enums", []):
        fn = item.get("fullName", "")
        if fn and fn not in seen_enums:
            seen_enums[fn] = build_enum(item)

    seen_xmlns = {}
    for item in shape_b.get("namespaceMappings", []):
        ns_key = (item.get("xmlNamespace", ""), item.get("sourceAssembly", ""))
        if ns_key not in seen_xmlns:
            seen_xmlns[ns_key] = build_xmlns_mapping(item)

    catalog = {
        "schema":            {"id": "activity-catalog", "version": "v0.3"},
        "source":            source_obj,
        "generatedAt":       generated_at,
        "activities":        activities,
        "enums":             list(seen_enums.values()),
        "namespaceMappings": list(seen_xmlns.values()),
    }

    out_path = OUT_LLMS_DIR / pkg_id / f"{version}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, catalog)

    member_count = sum(len(a["members"]) for a in activities)
    print(f"  {pkg_id}/{version}: {len(activities)} activities, "
          f"{member_count} members, {len(seen_enums)} enums -> {out_path}")
    return catalog


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build publishable activity-catalog v0.3 JSON from PackageFurnace enriched-catalog sources."
    )
    parser.add_argument("--set", metavar="ID", help="Build only packages from this set id")
    args = parser.parse_args()

    sets = config["sets"]
    if args.set:
        target_sets = [s for s in sets if s["id"] == args.set]
        if not target_sets:
            print(f"[error] No matching set found for --set={args.set!r}", file=sys.stderr)
            sys.exit(1)
    else:
        target_sets = sets

    pkg_versions = _collect_pkg_versions(target_sets)
    if not pkg_versions:
        print("[error] No packages resolved", file=sys.stderr)
        sys.exit(1)

    built_versions: dict[str, list[str]] = {}
    for pkg_id, versions in sorted(pkg_versions.items()):
        for version in versions:
            result = build_package(pkg_id, version)
            if result is not None:
                built_versions.setdefault(pkg_id, []).append(version)

    for pkg_id, versions in sorted(built_versions.items()):
        write_llms_txt(pkg_id, versions)

    total = sum(len(v) for v in built_versions.values())
    print(f"\nDone: {total} package/version catalogs, {len(built_versions)} llms.txt files "
          f"-> {OUT_LLMS_DIR}")


if __name__ == "__main__":
    main()
