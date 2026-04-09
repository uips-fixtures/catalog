# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "packaging", "psutil"]
# ///
"""
seed_latest.py — Extract PackageFurnace engine-results at latest-stable for
packages that have no explicit version pin in curated.yaml, or for the full
UiPath official feed.

This is the companion to extract_activities.py, which only handles pinned
versions.  Together they cover the full pipeline:

  extract_activities.py   pinned packages   (molten-dirigible, watchful-anvil, …)
  seed_latest.py          unpinned packages (reframework-core, studio-standard, …)
                          or full feed      (--full-feed)

Usage (from catalog/ root):
    uv run scripts/seed_latest.py                  # unpinned packages in curated.yaml
    uv run scripts/seed_latest.py --set <id>       # one set only
    uv run scripts/seed_latest.py --full-feed      # all packages in the feed
    uv run scripts/seed_latest.py --full-feed --dry-run

Options:
    --full-feed         Seed every package in feed-uipath-official.json
    --set <id>          Seed only unpinned packages in this curated set
    --no-suggested      Do not follow suggestedExtractions (avoids deep BFS)
    --mem-limit-gb N    Kill PackageFurnace if its process tree exceeds N GB RSS
                        (default: 10, set 0 to disable)
    --force             Re-extract even if a result file already exists
    --dry-run           Print plan without running PackageFurnace

Environment (same as extract_activities.py):
    PF_EXE     Path to PackageFurnace binary (default: per-platform well-known location)
    PF_CACHE   Path to nupkg download cache  (default: sibling of NUGET_PACKAGES or
                                               per-platform well-known location)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import psutil
import yaml
from packaging.version import Version

# ── Paths ──────────────────────────────────────────────────────────────────────

REPO_ROOT  = Path(__file__).resolve().parent.parent
CURATED    = REPO_ROOT / "config" / "curated.yaml"
FEEDS_DIR  = REPO_ROOT / "data" / "feeds" / "feed_index.json"
FEED_INDEX = REPO_ROOT / "data" / "sources" / "nuget" / "feed-uipath-official.json"
OUT_BASE   = REPO_ROOT / "data" / "sources" / "packagefurnace" / "pkg"


def default_pf_exe() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    suffix = ".exe" if sys.platform == "win32" else ""
    return base / "cpmf" / "tools" / "PackageFurnace" / f"PackageFurnace{suffix}"


def default_pf_cache() -> Path:
    nuget_packages = os.environ.get("NUGET_PACKAGES")
    if nuget_packages:
        return Path(nuget_packages).parent / "pf-cache"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "cpmf" / "pf-cache"


PF_EXE = Path(os.environ.get("PF_EXE", str(default_pf_exe())))
CACHE  = Path(os.environ.get("PF_CACHE", str(default_pf_cache())))

# ── Memory watcher ─────────────────────────────────────────────────────────────

def _watch_memory(proc: subprocess.Popen, limit_bytes: int, result: list) -> None:
    """Background thread: kill entire process tree if RSS exceeds limit_bytes.
    Appends peak RSS (bytes) to result on exit."""
    peak = 0
    try:
        ps = psutil.Process(proc.pid)
        while proc.poll() is None:
            try:
                children = ps.children(recursive=True)
                rss = sum(p.memory_info().rss for p in [ps] + children
                          if p.is_running())
                if rss > peak:
                    peak = rss
                if limit_bytes and rss > limit_bytes:
                    result.append(("killed", rss))
                    for child in reversed(children):
                        try: child.kill()
                        except psutil.NoSuchProcess: pass
                    try: ps.kill()
                    except psutil.NoSuchProcess: pass
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
            time.sleep(1)
    except Exception:
        pass
    result.append(("done", peak))


def run_pf(cmd: list[str], mem_limit_bytes: int) -> tuple[int, str, int]:
    """Run PackageFurnace, killing it if memory exceeds limit.
    Returns (returncode, stderr, peak_rss_bytes)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    watch_result: list = []
    if mem_limit_bytes:
        t = threading.Thread(target=_watch_memory,
                             args=(proc, mem_limit_bytes, watch_result), daemon=True)
        t.start()
    stdout, stderr = proc.communicate()
    rc = proc.returncode
    # Give watcher thread a moment to record peak
    if mem_limit_bytes:
        t.join(timeout=2)
    peak = watch_result[0][1] if watch_result else 0
    killed = watch_result and watch_result[0][0] == "killed"
    if killed:
        rc = -9  # sentinel for "killed by watcher"
    return rc, stderr, peak

# ── Feed helpers ───────────────────────────────────────────────────────────────

def build_feed_latest() -> dict[str, str]:
    """Return {package_id: latest_stable_version} from the cached feed index."""
    with open(FEED_INDEX, encoding="utf-8") as f:
        data = json.load(f)
    result: dict[str, str] = {}
    for item in data.get("items", []):
        pkg_id   = item["id"]
        versions = item.get("versions", [])
        stable   = [v for v in versions if not re.search(r"[a-zA-Z]", v)]
        if not stable:
            continue
        try:
            result[pkg_id] = str(max(stable, key=Version))
        except Exception:
            pass
    return result


def unpinned_curated_packages(curated: dict, set_filter: str | None) -> dict[str, str]:
    """Return {package_id: latest_stable_version} for every package in curated.yaml
    that has no explicit version pin, resolved against the feed."""
    feed_latest = build_feed_latest()
    ids: set[str] = set()
    for set_def in curated.get("sets", []):
        if set_def.get("builtins"):
            continue
        if set_filter and set_def["id"] != set_filter:
            continue
        for pkg in set_def.get("packages", []):
            if "version" not in pkg and "versions" not in pkg:
                ids.add(pkg["id"])
    result: dict[str, str] = {}
    missing: list[str] = []
    for pkg_id in sorted(ids):
        ver = feed_latest.get(pkg_id)
        if ver:
            result[pkg_id] = ver
        else:
            missing.append(pkg_id)
    if missing:
        print(f"[warn] {len(missing)} package(s) not in feed index (skipped):")
        for p in missing:
            print(f"       {p}")
    return result

# ── suggestedExtractions ───────────────────────────────────────────────────────

def collect_suggested(out_file: Path) -> list[tuple[str, str]]:
    try:
        d = json.loads(out_file.read_text(encoding="utf-8"))
        return [(e["id"], e["resolvedVersion"]) for e in d.get("suggestedExtractions", [])]
    except Exception:
        return []

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--full-feed", action="store_true",
                       help="Seed every package in the feed (ignores curated.yaml)")
    group.add_argument("--set", metavar="ID",
                       help="Seed only unpinned packages in this curated set")
    parser.add_argument("--no-suggested", action="store_true",
                        help="Do not follow suggestedExtractions")
    parser.add_argument("--mem-limit-gb", type=float, default=10.0, metavar="N",
                        help="Kill PackageFurnace if process-tree RSS exceeds N GB (default: 10, 0=disable)")
    parser.add_argument("--force",   action="store_true", help="Re-extract existing results")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without running PF")
    args = parser.parse_args()

    mem_limit_bytes = int(args.mem_limit_gb * 1024 ** 3) if args.mem_limit_gb else 0

    if not args.dry_run and not PF_EXE.exists():
        print(f"[error] PackageFurnace not found at {PF_EXE}")
        print("        Run `just install` in the PackageFurnace repo, or set PF_EXE.")
        return 1

    if args.full_feed:
        seed_map = build_feed_latest()
        scope_label = "full feed"
    else:
        with open(CURATED, encoding="utf-8") as f:
            curated = yaml.safe_load(f)
        seed_map = unpinned_curated_packages(curated, args.set)
        scope_label = f"set '{args.set}'" if args.set else "curated.yaml (unpinned)"

    if not seed_map:
        print("[warn] No packages to seed.")
        return 0

    mem_label = f"{args.mem_limit_gb:.0f} GB" if mem_limit_bytes else "unlimited"
    print(f"Seeding {len(seed_map)} package(s) at latest-stable  "
          f"[{scope_label}]  mem-limit={mem_label}\n")

    CACHE.mkdir(parents=True, exist_ok=True)

    seed = list(seed_map.items())
    queue:  deque[tuple[str, str]] = deque(seed)
    queued: set[tuple[str, str]]   = set(seed)

    ok = err = skip = killed_count = suggested_count = 0

    while queue:
        pid, ver = queue.popleft()

        out_dir  = OUT_BASE / pid
        out_file = out_dir / f"{ver}.json"
        out_dir.mkdir(parents=True, exist_ok=True)

        if out_file.exists() and not args.force:
            skip += 1
            if not args.no_suggested:
                for sug_id, sug_ver in collect_suggested(out_file):
                    pair = (sug_id, sug_ver)
                    if pair not in queued:
                        queued.add(pair)
                        queue.append(pair)
                        suggested_count += 1
            continue

        if args.dry_run:
            print(f"WOULD {pid} {ver}")
            ok += 1
            continue

        cmd = [str(PF_EXE),
               "--id", pid, "--pkg-version", ver,
               "--cache", str(CACHE), "--feeds", str(FEEDS_DIR),
               "--out", str(out_file)]

        rc, stderr, peak_rss = run_pf(cmd, mem_limit_bytes)

        peak_gb = peak_rss / 1024 ** 3

        if rc == -9:
            print(f"KILL {pid} {ver}  (mem {peak_gb:.1f} GB exceeded limit)")
            killed_count += 1
            err += 1
        elif rc == 0:
            print(f"OK   {pid} {ver}  ({peak_gb:.1f} GB peak)")
            ok += 1
            if not args.no_suggested:
                for sug_id, sug_ver in collect_suggested(out_file):
                    pair = (sug_id, sug_ver)
                    if pair not in queued:
                        queued.add(pair)
                        queue.append(pair)
                        suggested_count += 1
        else:
            print(f"FAIL {pid} {ver}  ({peak_gb:.1f} GB peak)")
            if stderr:
                print(stderr[:500])
            err += 1

        done = ok + err + skip
        if done % 50 == 0:
            print(f"  … {done} done ({ok} ok, {skip} skip, {err} fail"
                  + (f", {killed_count} killed" if killed_count else "")
                  + f") — {len(queue)} remaining in queue")

    label = "dry-run" if args.dry_run else "extracted"
    summary = f"\n{ok} {label}, {skip} skipped, {err} failed"
    if killed_count:
        summary += f" ({killed_count} killed for exceeding mem limit)"
    if suggested_count:
        summary += f", {suggested_count} companion(s) via suggestedExtractions"
    print(summary + ".")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
