# catalog justfile — pipeline tasks for activity-catalog data

# Seed latest-stable PackageFurnace results for every unpinned package in curated.yaml.
# Run this before `build` to ensure all latest-stable sets have up-to-date activity data.
seed:
    uv run scripts/seed_latest.py

# Seed a single set (e.g. `just seed-set reframework-core`)
seed-set set:
    uv run scripts/seed_latest.py --set {{set}}

# Seed every package in the full UiPath official feed at latest-stable (~904 packages).
seed-full:
    uv run scripts/seed_latest.py --full-feed

# Extract engine-results from NuGet packages via PackageFurnace CLI.
# PackageFurnace must be installed: run `just install` in the PackageFurnace repo.
# Override binary path with:  PF_EXE=/path/to/PackageFurnace just extract
# Override cache location with: PF_CACHE=/path/to/cache just extract
extract:
    uv run scripts/extract_activities.py

# Re-extract all packages, overwriting existing output files.
extract-force:
    uv run scripts/extract_activities.py --force

# Extract a single set (e.g. `just extract-set watchful-anvil`)
extract-set set:
    uv run scripts/extract_activities.py --set {{set}}

# Build publishable dist JSON from extracted engine-results.
build:
    uv run scripts/build_dist.py

# Build dist for a single set (e.g. `just build-set watchful-anvil`)
build-set set:
    uv run scripts/build_dist.py --set {{set}}

# Generate HTML developer reference docs from dist.
refdoc:
    uv run scripts/build_refdoc.py

# Generate LLM reference files (llms.txt) from dist.
refllm:
    uv run scripts/build_refllm.py

# Full pipeline: extract → build → refdoc + refllm
all: extract build refdoc refllm

# Full pipeline with forced re-extraction.
all-force: extract-force build refdoc refllm
