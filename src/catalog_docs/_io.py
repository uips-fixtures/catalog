"""Shared I/O utilities for catalog_docs."""

import datetime
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR  = REPO_ROOT / "data" / "dist"


def load_catalog(set_id: str) -> dict | None:
    path = DIST_DIR / set_id / "activities.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def discover_sets() -> list[str]:
    return sorted(
        d.name for d in DIST_DIR.iterdir()
        if d.is_dir() and (d / "activities.json").exists()
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
