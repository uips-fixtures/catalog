# /// script
# requires-python = ">=3.11"
# dependencies = ["jinja2"]
# ///
"""
build_refdoc.py — Generate HTML developer reference from activity-catalog dist JSON.

Output:
  data/dist/{set}/activities.html

Usage:
    uv run scripts/build_refdoc.py
    uv run scripts/build_refdoc.py --set watchful-anvil
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from catalog_docs._io import DIST_DIR, discover_sets, load_catalog, utc_now
from catalog_docs.refdoc import build_html, iter_packages


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate HTML developer reference from activity-catalog dist files."
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
            print(f"  [error] missing dist: {DIST_DIR / set_id / 'index.json'}", file=sys.stderr)
            continue
        for pkg_id, src, activities in iter_packages(catalog):
            html = build_html(set_id, pkg_id, src, activities, generated_at)
            out  = DIST_DIR / set_id / f"{pkg_id}.html"
            out.write_text(html, encoding="utf-8", newline="\n")
            members = sum(len(a["members"]) for a in activities)
            print(f"  {set_id}/{pkg_id}: {len(activities)} activities, {members} members -> {out}")


if __name__ == "__main__":
    main()
