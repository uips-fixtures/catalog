# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "packaging"]
# ///
"""
build_dist.py — Transform PackageFurnace engine-result v1 into activity-catalog v0.2 JSON.

Reads config/curated.yaml, resolves package versions, reads engine-result files from
data/sources/packagefurnace/pkg/{id}/{version}.json, maps activities/members/enums/
namespaceMappings, and writes data/dist/{set-id}/activities.json conforming to
schemas/v0.2/activity-catalog.schema.json.

Usage:
    uv run scripts/build_dist.py                    # all sets
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

REPO_ROOT    = Path(__file__).resolve().parent.parent
SHAPE_B_DIR  = REPO_ROOT / "data" / "sources" / "packagefurnace" / "pkg"
PKG_DATA_DIR = REPO_ROOT / "data" / "pkg"
OUT_DIR      = REPO_ROOT / "data" / "dist"
CONFIG_PATH  = REPO_ROOT / "config" / "curated.yaml"

# ── Config ────────────────────────────────────────────────────────────────────

config    = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
tfm_allow = set(config["defaults"]["targetFrameworks"]["include"])

# ── Version resolution ────────────────────────────────────────────────────────

def resolve_versions(pkg_id: str, pkg_cfg: dict) -> list[str]:
    """Return list of versions to include for this package entry."""
    if "version" in pkg_cfg:
        return [pkg_cfg["version"]]
    if "versions" in pkg_cfg:
        return list(pkg_cfg["versions"])
    # latest-stable: highest non-prerelease version present in SHAPE_B_DIR
    pkg_dir = SHAPE_B_DIR / pkg_id
    if not pkg_dir.exists():
        return []
    available = [p.stem for p in pkg_dir.glob("*.json")]
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

def map_property(prop: dict) -> dict | None:
    """Return a member dict, or None if the property should be excluded.

    Field mapping from PackageFurnace engine-result schema v1:
      isBrowsable       (was: browsable)
      isRequired        (was: isRequiredArgument)
      members           (was: properties)
    """
    if prop.get("isBrowsable") is False:
        return None

    raw_dt  = prop.get("dataType")             # null when absent in source
    arg_dir = prop.get("argumentDirection")   # "in" / "out" / "in-out" / None
    dt      = normalize_datatype(raw_dt) if raw_dt else None
    kind    = infer_member_kind(dt, arg_dir)

    return {
        "name":               prop["name"],
        "displayName":        prop.get("displayName") or prop["name"],
        "dataType":           dt,
        "memberKind":         kind,
        "argumentDirection":  arg_dir if kind == "argument" else None,
        "isRequiredArgument": bool(prop.get("isRequired")) if kind == "argument" else False,
        "description":        prop.get("description"),
        "category":           prop.get("category"),
        "defaultValue":       prop.get("defaultValue"),
        "typeConverter":      prop.get("typeConverter"),
    }

# ── Source and activity builders ───────────────────────────────────────────────

def build_source(shape_b: dict) -> dict:
    # engine-result v1: sourceId/sourceVersion at top level; package contains nuspec metadata
    pkg = shape_b.get("package", {})
    return {
        "kind":         "nuget-package",
        "id":           shape_b["sourceId"],
        "version":      shape_b["sourceVersion"],
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
        "members":               members,
        "enrichment":            None,
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

# ── Set builder ────────────────────────────────────────────────────────────────

def build_set(set_cfg: dict) -> None:
    set_id           = set_cfg["id"]
    generated_at     = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    per_pkg_catalogs = []

    for pkg_cfg in set_cfg["packages"]:
        pkg_id   = pkg_cfg["id"]
        versions = resolve_versions(pkg_id, pkg_cfg)

        if not versions:
            print(f"  [error] {pkg_id}: no versions resolved", file=sys.stderr)
            continue

        for version in versions:
            shape_b_path = SHAPE_B_DIR / pkg_id / f"{version}.json"
            if not shape_b_path.exists():
                print(f"  [error] missing engine-result: {shape_b_path}", file=sys.stderr)
                continue

            check_tfm(pkg_id, version)

            shape_b    = json.loads(shape_b_path.read_text(encoding="utf-8"))
            source_obj = build_source(shape_b)

            activities = [
                act for item in shape_b.get("activities", [])
                if item.get("visibility") != "hidden"   # kebab-case; hidden = not in Studio
                if (act := build_activity(item, source_obj)) is not None
            ]

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
                "schema":            {"id": "activity-catalog", "version": "v0.2"},
                "source":            source_obj,
                "generatedAt":       generated_at,
                "activities":        activities,
                "enums":             list(seen_enums.values()),
                "namespaceMappings": list(seen_xmlns.values()),
            }

            out_path = OUT_DIR / set_id / pkg_id / f"{version}.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(out_path, catalog)
            per_pkg_catalogs.append(catalog)

            member_count = sum(len(a["members"]) for a in activities)
            print(f"  {pkg_id}/{version}: {len(activities)} activities, "
                  f"{member_count} members, {len(seen_enums)} enums -> {out_path}")

    # ── Set index ──────────────────────────────────────────────────────────────
    set_index = [
        {"metadata": {
            "schema":      {"id": "activity-catalog-set", "version": "v0.2"},
            "set":         set_id,
            "generatedAt": generated_at,
        }},
        *per_pkg_catalogs,
    ]
    index_path = OUT_DIR / set_id / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(index_path, set_index)
    print(f"  {set_id}: index -> {index_path} ({len(per_pkg_catalogs)} packages)")

# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build publishable activity-catalog v0.2 JSON from PackageFurnace engine-result v1 sources."
    )
    parser.add_argument("--set", metavar="ID", help="Build only this set id")
    args = parser.parse_args()

    built = 0
    for set_cfg in config["sets"]:
        if args.set and set_cfg["id"] != args.set:
            continue
        print(f"Building set: {set_cfg['id']}")
        build_set(set_cfg)
        built += 1

    if built == 0:
        print(f"[error] No matching set found for --set={args.set!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
