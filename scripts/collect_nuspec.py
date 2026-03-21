"""
collect_nuspec.py — Download .nupkg for every (id, version) pair found in
data/sources/nuget/feed-*.json, extract the nuspec + manifest in one ZIP
pass, then rebuild data/sources/nuget/nuspec_catalog.json.

Usage (from repo root):
    uv run scripts/collect_nuspec.py

Environment variables:
    FORCE=true        Re-download even if cache files already exist (default: false)
    NUPKG_HOOK=<abs-path>  Optional local script called while each .nupkg is on disk
                           Receives: $1 = nupkg path; env: NUPKG_ID, NUPKG_VERSION,
                           NUPKG_PATH, NUPKG_FLAT2_BASE.  Exit code is ignored.
"""

import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).resolve().parent.parent
FEEDS_DIR   = REPO_ROOT / "data" / "sources" / "nuget"
NUSPEC_DIR  = REPO_ROOT / "data" / "nuspec"
CATALOG_OUT = FEEDS_DIR / "nuspec_catalog.json"

FORCE = os.environ.get("FORCE", "false").lower() == "true"
HOOK  = os.environ.get("NUPKG_HOOK", "")

CHUNK       = 65_536   # 64 KB download chunks — no RAM spike
SLEEP_SECS  = 0.05
LOG_EVERY   = 100

# ── Feed discovery ───────────────────────────────────────────────────────────

def discover_pairs() -> dict[tuple[str, str], str]:
    """Return {(id, version): flat2_base} from all feed-*.json files."""
    pairs: dict[tuple[str, str], str] = {}
    feed_files = sorted(glob.glob(str(FEEDS_DIR / "feed-*.json")))
    if not feed_files:
        print("[error] No feed-*.json files found in", FEEDS_DIR)
        sys.exit(1)

    for path in feed_files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        flat2_base = data.get("meta", {}).get("source", "")
        if not flat2_base:
            print(f"[warn] No meta.source in {path} — skipping")
            continue
        items = data.get("items", [])
        for item in items:
            pkg_id   = item.get("id", "")
            versions = item.get("versions", [])
            for v in versions:
                pairs[(pkg_id, v)] = flat2_base
        print(f"  {os.path.basename(path)}: {len(items)} packages")

    print(f"\nTotal unique (id, version) pairs: {len(pairs)}")
    return pairs

# ── HTTP helpers ─────────────────────────────────────────────────────────────

def stream_to_tempfile(url: str) -> str | None:
    """Stream url to a temp file. Returns path, or None on error."""
    req = urllib.request.Request(url, headers={"User-Agent": "uips-fixtures-catalog/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".nupkg") as tmp:
                while chunk := resp.read(CHUNK):
                    tmp.write(chunk)
                return tmp.name
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  [http {e.code}] {url}")
        return None
    except Exception as e:
        print(f"  [error] {url}: {e}")
        return None

# ── ZIP extraction ────────────────────────────────────────────────────────────

def _classify_entry(name: str) -> str:
    """Return the top-level folder key for a ZIP entry."""
    parts = name.split("/")
    if parts[0].lower() in ("lib", "ref", "analyzers", "build", "tools",
                             "contentfiles", "runtimes"):
        return parts[0].lower()
    return "other"


def extract_from_nupkg(
    tmppath: str,
    pkg_id: str,
    version: str,
    nuspec_path: Path,
    manifest_path: Path,
    props_path: Path,
    targets_path: Path,
) -> bool:
    """Open the nupkg once, extract everything, return True on success."""
    try:
        with zipfile.ZipFile(tmppath, "r") as zf:
            entries = zf.infolist()

            # ── nuspec ────────────────────────────────────────────────────
            nuspec_entry = next(
                (e for e in entries if e.filename.lower().endswith(".nuspec")), None
            )
            if nuspec_entry:
                nuspec_path.parent.mkdir(parents=True, exist_ok=True)
                nuspec_path.write_bytes(zf.read(nuspec_entry.filename))

            # ── props / targets ───────────────────────────────────────────
            for entry in entries:
                lo = entry.filename.lower()
                if lo.startswith("build/") and lo.endswith(".props"):
                    props_path.write_bytes(zf.read(entry.filename))
                elif lo.startswith("build/") and lo.endswith(".targets"):
                    targets_path.write_bytes(zf.read(entry.filename))

            # ── manifest ──────────────────────────────────────────────────
            manifest: dict[str, dict | list] = {
                "lib": {}, "ref": {}, "analyzers": {},
                "build": [], "tools": [], "other": [],
            }
            for entry in entries:
                name  = entry.filename
                parts = name.rstrip("/").split("/")
                key   = _classify_entry(name)

                if key in ("lib", "ref"):
                    tfm  = parts[1] if len(parts) > 1 else ""
                    file = parts[-1] if len(parts) > 2 else ""
                    if file and file.lower().endswith((".dll", ".exe", ".xml")):
                        manifest[key].setdefault(tfm, []).append(file)

                elif key == "analyzers":
                    # analyzers/{dotnet/cs}/{lang}/*.dll
                    subkey = "/".join(parts[1:-1]) if len(parts) > 2 else ""
                    file   = parts[-1]
                    if file.lower().endswith(".dll"):
                        manifest["analyzers"].setdefault(subkey, []).append(file)

                elif key == "build":
                    file = parts[-1]
                    if file and "." in file:
                        manifest["build"].append(file)

                elif key == "tools":
                    manifest["tools"].append(name)

                elif key == "other":
                    manifest["other"].append(name)

            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )

        return True
    except Exception as e:
        print(f"  [zip error] {pkg_id} {version}: {e}")
        return False

# ── Hook ─────────────────────────────────────────────────────────────────────

def run_hook(tmppath: str, pkg_id: str, version: str, flat2_base: str) -> None:
    if not HOOK or not os.path.isfile(HOOK):
        return
    env = {
        **os.environ,
        "NUPKG_ID":        pkg_id,
        "NUPKG_VERSION":   version,
        "NUPKG_PATH":      tmppath,
        "NUPKG_FLAT2_BASE": flat2_base,
    }
    subprocess.run(["python3", HOOK, tmppath], env=env, check=False)

# ── nuspec XML parsing ────────────────────────────────────────────────────────

def _ns(tag: str, elem_ns: str) -> str:
    return f"{{{elem_ns}}}{tag}" if elem_ns else tag


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_ns(parent: ET.Element, tag: str) -> ET.Element | None:
    """Find child by local tag name, ignoring namespace."""
    for child in parent:
        if _strip_ns(child.tag) == tag:
            return child
    return None


def _findtext_ns(parent: ET.Element, tag: str) -> str | None:
    el = _find_ns(parent, tag)
    return (el.text or "").strip() or None if el is not None else None


def _extract_license(meta: ET.Element) -> str | None:
    lic = _find_ns(meta, "license")
    if lic is not None:
        return (lic.text or "").strip() or lic.get("type")
    return _findtext_ns(meta, "licenseUrl")


def _extract_dependencies(meta: ET.Element) -> list[dict]:
    deps_el = _find_ns(meta, "dependencies")
    if deps_el is None:
        return []
    groups = []
    for group in deps_el:
        if _strip_ns(group.tag) != "group":
            continue
        tfm  = group.get("targetFramework", "")
        deps = []
        for dep in group:
            if _strip_ns(dep.tag) == "dependency":
                deps.append({"id": dep.get("id", ""), "version": dep.get("version", "")})
        groups.append({"targetFramework": tfm, "dependencies": deps})
    return groups


def parse_nuspec(path: Path) -> dict | None:
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        meta = _find_ns(root, "metadata")
        if meta is None:
            return None

        return {
            "id":               _findtext_ns(meta, "id"),
            "version":          _findtext_ns(meta, "version"),
            "authors":          _findtext_ns(meta, "authors"),
            "description":      _findtext_ns(meta, "description"),
            "tags":             _findtext_ns(meta, "tags"),
            "projectUrl":       _findtext_ns(meta, "projectUrl"),
            "license":          _extract_license(meta),
            "dependencyGroups": _extract_dependencies(meta),
        }
    except Exception as e:
        print(f"  [parse error] {path}: {e}")
        return None

# ── Catalog rebuild ───────────────────────────────────────────────────────────

def rebuild_catalog(now: datetime) -> None:
    print("\nRebuilding nuspec_catalog.json …")
    items   = []
    pkg_ids = set()

    for nuspec_path in sorted(NUSPEC_DIR.rglob("*.nuspec")):
        record = parse_nuspec(nuspec_path)
        if not record:
            continue

        manifest_path = nuspec_path.with_suffix(".manifest.json")
        if manifest_path.exists():
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            record["targetFrameworks"] = sorted(set(
                list(manifest.get("lib", {}).keys()) +
                list(manifest.get("ref", {}).keys())
            ))
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
            "nuspecCount":    len(items),
            "packageCount":   len(pkg_ids),
        },
        "items": items,
    }

    CATALOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CATALOG_OUT, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
        f.write("\n")

    print(f"  written: {CATALOG_OUT.relative_to(REPO_ROOT)} "
          f"({len(items)} nuspec entries, {len(pkg_ids)} packages)")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"collect_nuspec.py  FORCE={FORCE}  HOOK={HOOK or '(none)'}\n")

    pairs = discover_pairs()

    total     = len(pairs)
    skipped   = 0
    downloaded = 0
    failed    = 0

    for i, ((pkg_id, version), flat2_base) in enumerate(sorted(pairs.items())):
        nuspec_path   = NUSPEC_DIR / pkg_id / f"{version}.nuspec"
        manifest_path = NUSPEC_DIR / pkg_id / f"{version}.manifest.json"
        props_path    = NUSPEC_DIR / pkg_id / f"{version}.props"
        targets_path  = NUSPEC_DIR / pkg_id / f"{version}.targets"

        if not FORCE and nuspec_path.exists() and manifest_path.exists():
            skipped += 1
            if (i + 1) % LOG_EVERY == 0:
                print(f"  {i+1}/{total}  skipped={skipped}  downloaded={downloaded}  failed={failed}")
            continue

        url = (f"{flat2_base}/{pkg_id.lower()}/{version}"
               f"/{pkg_id.lower()}.{version}.nupkg")

        tmppath = stream_to_tempfile(url)
        if tmppath is None:
            print(f"  [warn] {pkg_id} {version}: not found or download error")
            failed += 1
            continue

        try:
            ok = extract_from_nupkg(
                tmppath, pkg_id, version,
                nuspec_path, manifest_path, props_path, targets_path,
            )
            if ok:
                run_hook(tmppath, pkg_id, version, flat2_base)
                downloaded += 1
            else:
                failed += 1
        finally:
            try:
                os.unlink(tmppath)
            except OSError:
                pass

        if (i + 1) % LOG_EVERY == 0:
            print(f"  {i+1}/{total}  skipped={skipped}  downloaded={downloaded}  failed={failed}")

        time.sleep(SLEEP_SECS)

    print(f"\nDone: {total} pairs — "
          f"downloaded={downloaded}  skipped={skipped}  failed={failed}")

    rebuild_catalog(now)


if __name__ == "__main__":
    main()
