# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "packaging"]
# ///
"""
build_dist.py — Transform Shape B package catalogs into publishable activity-catalog v0.1 JSON.

Reads config/curated.yaml, resolves package versions, reads Shape B files from
data/sources/nuget/pkg/{id}/{version}.json, maps properties[] → members[], and
writes data/dist/{set-id}/activities.json conforming to schemas/v0.1/activity-catalog.schema.json.

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

def infer_member_kind(data_type: str, arg_dir: str | None) -> str:
    if arg_dir:
        return "argument"
    if _CHILD_RE.search(data_type):
        return "child"
    if _VAR_RE.search(data_type):
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

    raw_dt  = prop.get("dataType") or ""
    arg_dir = prop.get("argumentDirection")   # "In" / "Out" / "InOut" / None
    dt      = normalize_datatype(raw_dt)
    kind    = infer_member_kind(dt, arg_dir)

    return {
        "name":              prop["name"],
        "displayName":       prop.get("displayName") or prop["name"],
        "dataType":          dt,
        "memberKind":        kind,
        "argumentDirection": arg_dir if kind == "argument" else None,
        "isRequiredArgument": bool(prop.get("isRequired")) if kind == "argument" else False,
        "description":       prop.get("description"),
        "category":          prop.get("category"),
    }

# ── Source and activity builders ───────────────────────────────────────────────

def build_source(shape_b: dict) -> dict:
    # engine-result v1: sourceId/sourceVersion at top level; package contains nuspec metadata
    pkg = shape_b.get("package", {})
    return {
        "kind":       "nuget-package",
        "id":         shape_b["sourceId"],
        "version":    shape_b["sourceVersion"],
        "authors":    pkg.get("authors") or None,
        "projectUrl": pkg.get("projectUrl") or None,
    }

def build_activity(item: dict, source_obj: dict) -> dict:
    # engine-result v1: members (was: properties); only Browsable/Legacy activities are useful
    members = [
        m for p in item.get("members", [])
        if (m := map_property(p)) is not None
    ]
    return {
        "id":          f"{item['fullName']}@{source_obj['id']}/{source_obj['version']}",
        "fullName":    item["fullName"],
        "displayName": item.get("displayName"),
        "description": item.get("description"),
        "category":    item.get("category"),
        "source":      source_obj,
        "members":     members,
    }

# ── Set builder ────────────────────────────────────────────────────────────────

def build_set(set_cfg: dict) -> None:
    set_id      = set_cfg["id"]
    sources     = []
    activities  = []
    seen_src    = {}   # (kind, id) → source_obj

    for pkg_cfg in set_cfg["packages"]:
        pkg_id   = pkg_cfg["id"]
        versions = resolve_versions(pkg_id, pkg_cfg)

        if not versions:
            print(f"  [error] {pkg_id}: no versions resolved", file=sys.stderr)
            continue

        for version in versions:
            shape_b_path = SHAPE_B_DIR / pkg_id / f"{version}.json"
            if not shape_b_path.exists():
                print(f"  [error] missing Shape B: {shape_b_path}", file=sys.stderr)
                continue

            check_tfm(pkg_id, version)

            shape_b    = json.loads(shape_b_path.read_text(encoding="utf-8"))
            source_obj = build_source(shape_b)
            key        = (source_obj["kind"], source_obj["id"])

            if key not in seen_src:
                seen_src[key] = source_obj
                sources.append(source_obj)

            # engine-result v1: activities (was: items); skip Hidden (not shown in Studio)
            for item in shape_b.get("activities", []):
                if item.get("visibility") == "Hidden":
                    continue
                activities.append(build_activity(item, source_obj))

    catalog = {
        "schema":      {"id": "activity-catalog", "version": "v0.1"},
        "set":         set_id,
        "generatedAt": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources":     sources,
        "activities":  activities,
    }

    out_path = OUT_DIR / set_id / "activities.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    utf8_no_bom = open(out_path, "w", encoding="utf-8", newline="\n")
    json.dump(catalog, utf8_no_bom, indent=2, ensure_ascii=False)
    utf8_no_bom.write("\n")
    utf8_no_bom.close()

    member_count = sum(len(a["members"]) for a in activities)
    print(f"  {set_id}: {len(activities)} activities, {member_count} members -> {out_path}")

# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build publishable activity-catalog v0.1 JSON from Shape B sources."
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
