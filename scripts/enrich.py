# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
enrich.py — Run the PackageFurnace full pipeline (unpack → map-deps → index-types →
merge-index → enrich) to produce enriched-catalog.json in pf-cache.

Usage:
    uv run scripts/enrich.py <pkg-id> <version>
    uv run scripts/enrich.py --all          # enrich every pkg/version in data/sources/packagefurnace/pkg/

Environment:
    PF_EXE    Path to PackageFurnace binary (default: per-platform well-known location)
    PF_CACHE  pf-cache root directory       (default: sibling of NUGET_PACKAGES or
                                             per-platform well-known location)
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "data" / "sources" / "packagefurnace" / "pkg"


def default_pf_exe() -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    local  = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME", "~")
    return Path(local) / "cpmf" / "tools" / "PackageFurnace" / f"PackageFurnace{suffix}"


def default_pf_cache() -> Path:
    nuget_packages = os.environ.get("NUGET_PACKAGES")
    if nuget_packages:
        return Path(nuget_packages).parent / "pf-cache"
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME", "~")
    return Path(local) / "cpmf" / "pf-cache"


PF_EXE   = Path(os.environ.get("PF_EXE",   str(default_pf_exe())))
PF_CACHE = Path(os.environ.get("PF_CACHE", str(default_pf_cache())))


def enrich(pkg_id: str, version: str, force: bool = False) -> bool:
    """Run `pf pipeline run-all` for one (pkg_id, version). Returns True on success."""
    enriched = PF_CACHE / pkg_id.lower() / version / "enriched-catalog.json"
    if enriched.exists() and not force:
        print(f"  [skip] {pkg_id}/{version}: enriched-catalog.json already present")
        return True

    cmd = [
        str(PF_EXE),
        "pipeline", "run-all",
        "--cache", str(PF_CACHE),
        "--id",    pkg_id,
        "--pkg-version", version,
    ]
    if force:
        cmd.append("--force")

    print(f"  enrich {pkg_id}/{version} ...", flush=True)
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"  [error] {pkg_id}/{version}: exit {result.returncode}", file=sys.stderr)
        return False
    print(f"  [ok] {pkg_id}/{version} -> {enriched}")
    return True


def all_source_packages() -> list[tuple[str, str]]:
    """Return [(pkg_id, version), ...] for every stable version in SOURCE_DIR."""
    entries = []
    if not SOURCE_DIR.exists():
        return entries
    for pkg_dir in sorted(SOURCE_DIR.iterdir()):
        if not pkg_dir.is_dir():
            continue
        for json_file in sorted(pkg_dir.glob("*.json")):
            ver = json_file.stem
            if not re.search(r"[a-zA-Z]", ver):
                entries.append((pkg_dir.name, ver))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run PackageFurnace full pipeline to produce enriched-catalog.json."
    )
    parser.add_argument("id",      nargs="?", help="NuGet package id")
    parser.add_argument("version", nargs="?", help="Package version")
    parser.add_argument("--all",   action="store_true",
                        help="Enrich every package/version in data/sources/packagefurnace/pkg/")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even if enriched-catalog.json already exists")
    args = parser.parse_args()

    if not PF_EXE.exists():
        print(f"[error] PackageFurnace not found at {PF_EXE}", file=sys.stderr)
        print("        Run `just install` in the PackageFurnace repo, or set PF_EXE.", file=sys.stderr)
        sys.exit(1)

    if args.all:
        packages = all_source_packages()
        if not packages:
            print(f"[error] No source packages found in {SOURCE_DIR}", file=sys.stderr)
            sys.exit(1)
        print(f"Enriching {len(packages)} package/version(s) from {SOURCE_DIR}")
        ok = fail = 0
        for pkg_id, version in packages:
            if enrich(pkg_id, version, force=args.force):
                ok += 1
            else:
                fail += 1
        print(f"\nDone: {ok} ok, {fail} failed")
        if fail:
            sys.exit(1)
    elif args.id and args.version:
        print(f"Enriching {args.id}/{args.version}")
        if not enrich(args.id, args.version, force=args.force):
            sys.exit(1)
    else:
        parser.error("Provide <id> <version> or --all")


if __name__ == "__main__":
    main()
