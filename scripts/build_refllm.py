# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2"]
# ///
"""
build_refllm.py — Generate LLM reference files (llms.txt) from activity-catalog dist JSON.

Output:
  data/dist/{set}/{pkg_id}.txt

Usage:
    uv run scripts/build_refllm.py
    uv run scripts/build_refllm.py --set watchful-anvil
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from catalog_docs._io import DIST_DIR, discover_sets, load_catalog, utc_now
from catalog_docs.refdoc import iter_packages
from catalog_docs.refllm import build_refllm


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate LLM reference files from activity-catalog dist files."
    )
    parser.add_argument("--set", metavar="ID", help="Process only this set id")
    args = parser.parse_args()

    set_ids = discover_sets()
    if not set_ids:
        print("[error] No dist files found. Run build_dist.py first.", file=sys.stderr)
        sys.exit(1)

    if args.set:
        if args.set not in set_ids:
            print(f"[error] Set {args.set!r} not found in {DIST_DIR}", file=sys.stderr)
            sys.exit(1)
        set_ids = [args.set]

    generated_at = utc_now()
    for set_id in set_ids:
        catalog = load_catalog(set_id)
        if catalog is None:
            print(f"  [error] missing dist: {DIST_DIR / set_id / 'activities.json'}", file=sys.stderr)
            continue
        parts = []
        for pkg_id, src, activities in iter_packages(catalog):
            txt = build_refllm(set_id, pkg_id, src, activities, generated_at)
            out = DIST_DIR / set_id / f"{pkg_id}.txt"
            out.write_text(txt, encoding="utf-8", newline="\n")
            print(f"  {set_id}/{pkg_id}: {len(activities)} activities -> {out}")
            parts.append(txt)
        combined = "\n\n---\n\n".join(parts)
        llms_path = DIST_DIR / set_id / "llms.txt"
        llms_path.write_text(combined, encoding="utf-8", newline="\n")
        print(f"  {set_id}: llms.txt ({len(parts)} packages) -> {llms_path}")


if __name__ == "__main__":
    main()
