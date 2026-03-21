# catalog

**Public.** Single source of truth for all uips-fixtures MCVEs and UiPath activity package metadata. Derivative output formats are generated from this repo.

## Responsibility

- Receives sanitised data from source pipelines
- Sanitisation (stripping local paths and internal identifiers) happens upstream, before data reaches this repo
- Aggregation and derivative-build workflows run here against clean data

## Data flow

```
source pipelines (sanitise before push)
      │
      ▼
data/sources/{domain}/    ← sanitised source files
      │
      │  aggregate workflow
      ▼
data/aggregated/          ← single source of truth
      │
      │  publish workflow
      ▼
dist/                     ← derivative outputs staged here
      │
      ▼
→ uips-fixtures.github.io ← pushed by publish workflow, served as GitHub Pages
```

## Structure

```
data/
  sources/
    nuget/          ← sanitised UiPath activity package catalog files
    org/            ← GitHub org metadata
    mcve/           ← MCVE repo metadata
    interactions/   ← stars, forks, PR activity
  aggregated/       ← built by aggregate workflow, do not edit manually
dist/               ← built by publish workflow, then pushed to uips-fixtures.github.io
schemas/            ← JSON Schema for each domain and aggregated output
```

Do not edit `data/aggregated/` or `dist/` manually. All writes are performed by GitHub Actions.
