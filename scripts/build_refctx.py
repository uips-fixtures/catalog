# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2"]
# ///
"""
build_refctx.py — Generate LLM reference context (llms.txt) from activity-catalog dist JSON.

Output:
  data/dist/{set}/llms.txt

Usage:
    uv run scripts/build_refctx.py
    uv run scripts/build_refctx.py --set watchful-anvil
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from catalog_docs._io import DIST_DIR, discover_sets, load_catalog, utc_now
from catalog_docs.refdoc import iter_packages
from catalog_docs.refctx import build_refctx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate LLM reference context from activity-catalog dist files."
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
        for pkg_id, src, activities in iter_packages(catalog):
            txt = build_refctx(set_id, pkg_id, src, activities, generated_at)
            out = DIST_DIR / set_id / f"{pkg_id}.txt"
            out.write_text(txt, encoding="utf-8", newline="\n")
            print(f"  {set_id}/{pkg_id}: {len(activities)} activities -> {out}")


if __name__ == "__main__":
    main()
