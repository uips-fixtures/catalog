"""Shared I/O utilities for catalog_docs."""

import datetime
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR  = REPO_ROOT / "data" / "dist"


def load_catalog(set_id: str) -> list[dict] | None:
    """Return the list of per-package catalog dicts for a set.

    Reads data/dist/{set_id}/index.json (current format: list where entry 0
    is the metadata header and entries 1..N are per-package catalogs).
    Returns None when the file is absent.
    """
    path = DIST_DIR / set_id / "index.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Skip entry 0 (metadata header); return only per-package catalog entries.
    return [entry for entry in raw if "source" in entry]


def discover_sets() -> list[str]:
    return sorted(
        d.name for d in DIST_DIR.iterdir()
        if d.is_dir() and (d / "index.json").exists()
    )


def group_by_package(activities: list[dict]) -> dict[str, list[dict]]:
    """Return {source_id: [activity, ...]} preserving source order."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for act in activities:
        groups[act["source"]["id"]].append(act)
    return groups


def normalize_type(dt: str) -> str:
    """Shorten verbose .NET types: drop common namespace prefixes."""
    for prefix in (
        "System.Activities.",
        "System.Collections.Generic.",
        "System.Collections.ObjectModel.",
        "System.",
    ):
        dt = dt.replace(prefix, "")
    return dt


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
