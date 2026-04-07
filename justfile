# catalog justfile — pipeline tasks for activity-catalog data

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
