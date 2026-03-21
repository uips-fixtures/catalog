"""
collect_nuspec.py — Download .nupkg for every (id, version) pair found in
data/sources/nuget/feed-*.json, extract artifacts in one ZIP pass, then
delete the temp file.

Does ONE thing: fetch and cache raw artifacts.
Parsing / catalog building is handled by build_nuspec_catalog.py.

Usage (from repo root):
    uv run scripts/collect_nuspec.py

Environment variables:
    FORCE=true         Re-download even if cache files already exist (default: false)
    NUPKG_HOOK=<path>  Absolute path to a local script called while each .nupkg is
                       on disk. The hook is NOT committed to the repo.

                       Supported extensions:
                         .ps1  — called via:  pwsh -File <hook>
                                              -NupkgPath  <nupkg_path>
                                              -PackageSearchRoot <work_dir>
                                              -OutputPath <data/nuspec/{id}/{ver}.catalog.json>
                         .py   — called via:  python3 <hook> <nupkg_path>
                         other — called directly: <hook> <nupkg_path>

                       All hooks also receive these environment variables:
                         NUPKG_ID           package id
                         NUPKG_VERSION      version string
                         NUPKG_PATH         absolute path to the .nupkg in NUPKG_WORK_DIR
                         NUPKG_WORK_DIR     temp dir containing the .nupkg (and any resolved
                                            runtime packages) — use as PackageSearchRoot
                         NUPKG_OUTPUT_PATH  suggested output path for catalog files
                         NUPKG_FLAT2_BASE   feed base URL

Stop: Ctrl-C / SIGTERM finishes the current download then exits cleanly.
"""

import glob
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

REPO_ROOT  = Path(__file__).resolve().parent.parent
FEEDS_DIR  = REPO_ROOT / "data" / "sources" / "nuget"
NUSPEC_DIR = REPO_ROOT / "data" / "nuspec"

FORCE    = os.environ.get("FORCE", "false").lower() == "true"
HOOK     = os.environ.get("NUPKG_HOOK", "")
# Comma-separated package id filter, e.g. PACKAGES=UiPath.UIAutomation.Activities,CoreWF
PACKAGES = [p.strip() for p in os.environ.get("PACKAGES", "").split(",") if p.strip()]

# Never call the hook on CI (GitHub Actions sets CI=true)
if os.environ.get("CI", "").lower() in ("true", "1", "yes") and HOOK:
    print(f"[info] NUPKG_HOOK ignored on CI: {HOOK}")
    HOOK = ""

SLEEP_SECS = 0.05
LOG_EVERY  = 100

# ── Graceful stop ─────────────────────────────────────────────────────────────

_stop = False


def _handle_stop(signum, frame):
    global _stop
    if not _stop:
        print("\n[signal] Stop requested — finishing current download then exiting …",
              flush=True)
        _stop = True


signal.signal(signal.SIGINT,  _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)

# ── Feed discovery ────────────────────────────────────────────────────────────

def discover_pairs() -> dict[tuple[str, str], str]:
    """Return {(id, version): flat2_base} from all feed-*.json files."""
    pairs: dict[tuple[str, str], str] = {}
    feed_files = sorted(glob.glob(str(FEEDS_DIR / "feed-*.json")))
    if not feed_files:
        print("[error] No feed-*.json files found in", FEEDS_DIR, file=sys.stderr)
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
        print(f"  {os.path.basename(path)}: {len(items)} packages, "
              f"{sum(len(i.get('versions', [])) for i in items)} pairs")

    print(f"\nTotal unique (id, version) pairs: {len(pairs)}")
    return pairs

# ── HTTP download ─────────────────────────────────────────────────────────────

_HEADERS = {"User-Agent": "uips-fixtures-catalog/1.0"}


def _fetch(url: str) -> bytes | None:
    """Download url into memory. Returns bytes or None on 404/error."""
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  [http {e.code}] {url}")
        return None
    except Exception as e:
        print(f"  [error] {url}: {e}")
        return None


def _download_to_file(url: str, dest: Path) -> bool:
    """Download url to dest file on disk. Returns True on success."""
    data = _fetch(url)
    if data is None:
        return False
    dest.write_bytes(data)
    return True

# ── Runtime resolution ────────────────────────────────────────────────────────

def resolve_runtimes(
    pkg_id: str,
    version: str,
    pairs: dict[tuple[str, str], str],
    work_dir: Path,
) -> None:
    """
    Download runtime-companion packages to work_dir alongside the design package.

    Heuristic: packages whose id starts with the same prefix as pkg_id AND whose
    id contains 'runtime' (case-insensitive) AND whose version matches exactly.

    These are placed in work_dir so the hook can use it as -PackageSearchRoot.
    """
    prefix = pkg_id.lower()
    for (rid, rver), rbase in pairs.items():
        if rver != version:
            continue
        rid_lo = rid.lower()
        if rid_lo == prefix:
            continue  # that's the design package itself
        if not rid_lo.startswith(prefix):
            continue
        if "runtime" not in rid_lo:
            continue
        dest = work_dir / f"{rid.lower()}.{rver}.nupkg"
        if dest.exists():
            continue
        url = f"{rbase}/{rid.lower()}/{rver}/{rid.lower()}.{rver}.nupkg"
        ok  = _download_to_file(url, dest)
        if ok:
            print(f"    runtime: {rid} {rver}")

# ── ZIP extraction ────────────────────────────────────────────────────────────

def _classify(name: str) -> str:
    top = name.split("/")[0].lower()
    return top if top in ("lib", "ref", "analyzers", "build", "tools") else "other"


def extract_artifacts(
    nupkg: bytes | Path,
    pkg_id: str,
    version: str,
    out_dir: Path,
) -> bool:
    """Single ZIP pass: extract nuspec, manifest, build props/targets.
    nupkg may be raw bytes (in-memory) or a Path to a file on disk."""
    try:
        source = io.BytesIO(nupkg) if isinstance(nupkg, bytes) else nupkg
        with zipfile.ZipFile(source, "r") as zf:
            entries = zf.infolist()

            # nuspec
            nuspec_entry = next(
                (e for e in entries if e.filename.lower().endswith(".nuspec")), None
            )
            if nuspec_entry:
                (out_dir / f"{version}.nuspec").write_bytes(
                    zf.read(nuspec_entry.filename)
                )

            # build props / targets
            for e in entries:
                lo = e.filename.lower()
                if lo.startswith("build/") and lo.endswith(".props"):
                    (out_dir / f"{version}.props").write_bytes(zf.read(e.filename))
                elif lo.startswith("build/") and lo.endswith(".targets"):
                    (out_dir / f"{version}.targets").write_bytes(zf.read(e.filename))

            # manifest
            manifest: dict = {
                "lib": {}, "ref": {}, "analyzers": {},
                "build": [], "tools": [], "other": [],
            }
            for e in entries:
                name  = e.filename
                parts = name.rstrip("/").split("/")
                key   = _classify(name)

                if key in ("lib", "ref"):
                    tfm  = parts[1] if len(parts) > 1 else ""
                    file = parts[-1] if len(parts) > 2 else ""
                    if file and file.lower().endswith((".dll", ".exe", ".xml")):
                        manifest[key].setdefault(tfm, []).append(file)

                elif key == "analyzers":
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

                else:
                    manifest["other"].append(name)

            (out_dir / f"{version}.manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )

        return True
    except Exception as e:
        print(f"  [zip error] {pkg_id} {version}: {e}")
        return False

# ── Hook ──────────────────────────────────────────────────────────────────────

def run_hook(
    nupkg_path: Path,
    work_dir: Path,
    pkg_id: str,
    version: str,
    flat2_base: str,
    out_dir: Path,
) -> None:
    if not HOOK or not os.path.isfile(HOOK):
        return

    output_path = str(out_dir / f"{version}.catalog.json")

    env = {
        **os.environ,
        "NUPKG_ID":          pkg_id,
        "NUPKG_VERSION":     version,
        "NUPKG_PATH":        str(nupkg_path),
        "NUPKG_WORK_DIR":    str(work_dir),
        "NUPKG_OUTPUT_PATH": output_path,
        "NUPKG_FLAT2_BASE":  flat2_base,
    }

    hook_lo = HOOK.lower()
    if hook_lo.endswith(".ps1"):
        cmd = [
            "pwsh", "-File", HOOK,
            "-NupkgPath",        str(nupkg_path),
            "-PackageSearchRoot", str(work_dir),
            "-OutputPath",        output_path,
        ]
    elif hook_lo.endswith(".py"):
        cmd = ["python3", HOOK, str(nupkg_path)]
    else:
        cmd = [HOOK, str(nupkg_path)]

    subprocess.run(cmd, env=env, check=False)

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    pkg_filter = {p.lower() for p in PACKAGES}
    print(f"collect_nuspec.py  FORCE={FORCE}  HOOK={HOOK or '(none)'}"
          + (f"  PACKAGES={','.join(PACKAGES)}" if PACKAGES else "") + "\n")

    all_pairs = discover_pairs()

    # Apply package filter if set
    pairs = {k: v for k, v in all_pairs.items() if not pkg_filter or k[0].lower() in pkg_filter}
    if pkg_filter:
        print(f"Filtered to {len(pairs)} pairs for: {', '.join(PACKAGES)}\n")

    total   = len(pairs)
    skipped = downloaded = failed = 0

    for i, ((pkg_id, version), flat2_base) in enumerate(sorted(pairs.items())):
        if _stop:
            break

        out_dir       = NUSPEC_DIR / pkg_id
        nuspec_path   = out_dir / f"{version}.nuspec"
        manifest_path = out_dir / f"{version}.manifest.json"

        if not FORCE and nuspec_path.exists() and manifest_path.exists():
            skipped += 1
            if (i + 1) % LOG_EVERY == 0:
                print(f"  {i+1}/{total}  skip={skipped}  dl={downloaded}  err={failed}")
            continue

        nupkg_name = f"{pkg_id.lower()}.{version}.nupkg"
        url        = f"{flat2_base}/{pkg_id.lower()}/{version}/{nupkg_name}"

        if HOOK:
            # Hook needs file on disk — use named temp work dir
            work_dir        = Path(tempfile.mkdtemp(prefix="nuspec-"))
            nupkg_path_disk = work_dir / nupkg_name
            ok = _download_to_file(url, nupkg_path_disk)
            if not ok:
                print(f"  [warn] {pkg_id} {version}: not found or download error")
                shutil.rmtree(work_dir, ignore_errors=True)
                failed += 1
                continue
            nupkg_src: bytes | Path = nupkg_path_disk
        else:
            # No hook — load into memory, no temp file
            work_dir = None
            data     = _fetch(url)
            if data is None:
                print(f"  [warn] {pkg_id} {version}: not found or download error")
                failed += 1
                continue
            nupkg_src = data

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            ok = extract_artifacts(nupkg_src, pkg_id, version, out_dir)
            if ok:
                if HOOK and work_dir:
                    resolve_runtimes(pkg_id, version, all_pairs, work_dir)
                    run_hook(nupkg_path_disk, work_dir, pkg_id, version, flat2_base, out_dir)
                downloaded += 1
            else:
                failed += 1
        finally:
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)

        if (i + 1) % LOG_EVERY == 0:
            print(f"  {i+1}/{total}  skip={skipped}  dl={downloaded}  err={failed}")

        time.sleep(SLEEP_SECS)

    print(f"\nDone: {total} pairs — dl={downloaded}  skip={skipped}  err={failed}")
    if _stop:
        print("Stopped early. Re-run to continue (cached files won't be re-downloaded).")


if __name__ == "__main__":
    main()
