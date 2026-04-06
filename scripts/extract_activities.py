# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""
extract_activities.py — Run PackageFurnace for a set of pinned packages,
then follow suggestedExtractions until no new packages are queued.

Usage:
    uv run scripts/extract_activities.py
    uv run scripts/extract_activities.py --set watchful-anvil
    uv run scripts/extract_activities.py --skip-builtins
    uv run scripts/extract_activities.py --force

Reads:  config/curated.yaml  (for set definitions)
Writes: data/sources/packagefurnace/pkg/{id}/{version}.json

PackageFurnace CLI is expected at its default install location:
  %LOCALAPPDATA%/cpmf/tools/PackageFurnace/PackageFurnace.exe  (Windows)
  ~/.local/share/cpmf/tools/PackageFurnace/PackageFurnace      (Linux/macOS)
Override with PF_EXE env var.
"""

import json
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

import yaml

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent   # catalog/ root
CURATED   = REPO_ROOT / "config" / "curated.yaml"
FEEDS     = REPO_ROOT / "data" / "feeds" / "feed_index.json"
CACHE     = REPO_ROOT / ".cache"
OUT_BASE  = REPO_ROOT / "data" / "sources" / "packagefurnace" / "pkg"

def default_pf_exe() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    suffix = ".exe" if sys.platform == "win32" else ""
    return base / "cpmf" / "tools" / "PackageFurnace" / f"PackageFurnace{suffix}"

PF_EXE = Path(os.environ.get("PF_EXE", str(default_pf_exe())))

# ── Package resolution ─────────────────────────────────────────────────────────

def collect_pairs(
    curated: dict,
    set_filter: str | None = None,
    skip_builtins: bool = False,
) -> dict[tuple[str, str], dict]:
    """Return {(id, version): flags} from curated.yaml explicit pins.

    flags is a dict that may contain:
      legacy_browsable: bool  — whether --legacy-browsable should be passed

    When set_filter is given, only that set's packages are included — EXCEPT for
    sets marked builtins: true, which are always included unless skip_builtins=True.
    """
    pairs: dict[tuple[str, str], dict] = {}
    for set_def in curated.get("sets", []):
        is_builtins = bool(set_def.get("builtins", False))
        if skip_builtins and is_builtins:
            continue
        if set_filter and set_def["id"] != set_filter and not is_builtins:
            continue
        flags = {"legacy_browsable": set_def.get("legacyBrowsable", False)}
        for pkg in set_def.get("packages", []):
            pid = pkg["id"]
            if "version" in pkg:
                key = (pid, pkg["version"])
                pairs.setdefault(key, flags)
            elif "versions" in pkg:
                for ver in pkg["versions"]:
                    key = (pid, ver)
                    pairs.setdefault(key, flags)
    return pairs

def collect_suggested(out_file: Path) -> list[tuple[str, str]]:
    """Return [(id, version)] from suggestedExtractions in a result file."""
    try:
        d = json.loads(out_file.read_text(encoding="utf-8"))
        return [(e["id"], e["resolvedVersion"]) for e in d.get("suggestedExtractions", [])]
    except Exception:
        return []

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Extract PackageFurnace engine-results for all curated sets.")
    parser.add_argument("--set", metavar="ID",
                        help="Extract only this set (builtins sets are always included unless --skip-builtins)")
    parser.add_argument("--skip-builtins", action="store_true",
                        help="Do not extract sets marked builtins: true in curated.yaml")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract even if output file already exists")
    args = parser.parse_args()

    if not PF_EXE.exists():
        print(f"[error] PackageFurnace not found at {PF_EXE}")
        print("        Run `just install` in the PackageFurnace repo, or set PF_EXE.")
        return 1

    with open(CURATED) as f:
        curated = yaml.safe_load(f)

    seed_pairs = collect_pairs(curated, set_filter=args.set, skip_builtins=args.skip_builtins)
    if not seed_pairs:
        print("[warn] No explicitly pinned packages found in curated.yaml.")
        return 0

    CACHE.mkdir(parents=True, exist_ok=True)

    # BFS queue: start from explicitly pinned packages, fan out via suggestedExtractions.
    # Each entry is (id, version, flags). Flags carry per-set options (e.g. legacy_browsable).
    # Suggested companions inherit no flags (they are not in any curated set).
    queue: deque[tuple[str, str, dict]] = deque(
        (pid, ver, flags) for (pid, ver), flags in seed_pairs.items()
    )
    queued: set[tuple[str, str]] = set(seed_pairs.keys())

    ok = err = suggested_count = 0

    while queue:
        pid, ver, flags = queue.popleft()

        out_dir  = OUT_BASE / pid
        out_file = out_dir / f"{ver}.json"
        out_dir.mkdir(parents=True, exist_ok=True)

        if out_file.exists() and not args.force:
            print(f"SKIP {pid} {ver}  (already extracted)")
            for sug_id, sug_ver in collect_suggested(out_file):
                pair = (sug_id, sug_ver)
                if pair not in queued:
                    queued.add(pair)
                    queue.append((sug_id, sug_ver, {}))
                    suggested_count += 1
            continue

        cmd = [str(PF_EXE),
               "--id", pid, "--pkg-version", ver,
               "--cache", str(CACHE), "--feeds", str(FEEDS),
               "--out", str(out_file)]
        if flags.get("legacy_browsable"):
            cmd.append("--legacy-browsable")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"OK   {pid} {ver}")
            ok += 1
            for sug_id, sug_ver in collect_suggested(out_file):
                pair = (sug_id, sug_ver)
                if pair not in queued:
                    queued.add(pair)
                    queue.append((sug_id, sug_ver, {}))
                    suggested_count += 1
        else:
            print(f"FAIL {pid} {ver}")
            print(result.stderr[:500])
            err += 1

    print(f"\n{ok} succeeded, {err} failed, {suggested_count} companion(s) queued via suggestedExtractions.")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
