# catalog justfile — single source of pipeline orchestration
#
# Full pipeline:
#   seed → extract → enrich → build → refdoc
#
# Environment variables (all have working defaults):
#   PF_EXE    Path to PackageFurnace binary
#             default: %LOCALAPPDATA%\cpmf\tools\PackageFurnace\PackageFurnace.exe
#             install:  `just install` in the PackageFurnace repo
#   PF_CACHE  pf-cache root (nupkg download cache + pipeline artefacts)
#             default: sibling of %NUGET_PACKAGES% or %LOCALAPPDATA%\cpmf\pf-cache

# ── Seed ──────────────────────────────────────────────────────────────────────

# Seed latest-stable engine-results for every unpinned package in curated.yaml.
seed:
    uv run scripts/seed_latest.py

# Seed a single set (e.g. `just seed-set reframework-stable`)
seed-set set:
    uv run scripts/seed_latest.py --set {{set}}

# Seed every package in the full UiPath official feed at latest-stable.
seed-full:
    uv run scripts/seed_latest.py --full-feed

# Seed every stable version of every package in the feed.
seed-all-versions:
    uv run scripts/seed_latest.py --all-versions

# ── Extract ───────────────────────────────────────────────────────────────────

# Extract engine-results for pinned packages via PackageFurnace CLI.
extract:
    uv run scripts/extract_activities.py

# Re-extract all pinned packages, overwriting existing output files.
extract-force:
    uv run scripts/extract_activities.py --force

# Extract a single set (e.g. `just extract-set watchful-anvil`)
extract-set set:
    uv run scripts/extract_activities.py --set {{set}}

# ── Enrich ────────────────────────────────────────────────────────────────────

# Run the full PackageFurnace pipeline (unpack → map-deps → index-types → merge-index → enrich)
# for a single package/version. Writes enriched-catalog.json to pf-cache.
# Usage: just enrich UiPath.System.Activities 26.2.4
enrich id version:
    uv run scripts/enrich.py {{id}} {{version}} --timeout 300

# Run enrich for every package/version present in data/sources/packagefurnace/pkg/.
# Add --timeout SECONDS to kill hung packages (recommended: 300).
enrich-all:
    uv run scripts/enrich.py --all --timeout 300

# ── Build ─────────────────────────────────────────────────────────────────────

# Build publishable dist JSON. Prefers enriched-catalog.json when PF_CACHE is set.
build:
    uv run scripts/build_dist.py

# Build dist for a single set (e.g. `just build-set reframework-stable`)
build-set set:
    uv run scripts/build_dist.py --set {{set}}

# ── Refdoc ────────────────────────────────────────────────────────────────────

# Generate HTML reference + index pages from dist.
refdoc:
    uv run scripts/build_refdoc.py

# ── Full pipelines ────────────────────────────────────────────────────────────

# Full pipeline: seed → extract → enrich-all → build → refdoc
all: seed extract enrich-all build refdoc

# Full pipeline with forced re-extraction.
all-force: seed extract-force enrich-all build refdoc
